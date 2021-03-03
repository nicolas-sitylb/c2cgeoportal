# -*- coding: utf-8 -*-

# Copyright (c) 2011-2021, Camptocamp SA
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# The views and conclusions contained in the software and documentation are those
# of the authors and should not be interpreted as representing official policies,
# either expressed or implied, of the FreeBSD Project.


import json
import os
import re
import subprocess
import traceback
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Type, cast
from xml.dom import Node
from xml.parsers.expat import ExpatError

import requests
import sqlalchemy
import yaml
from bottle import MakoTemplate, template
from c2c.template.config import config
from defusedxml.minidom import parseString
from geoalchemy2.types import Geometry
from lingua.extractors import Extractor, Message
from mako.lookup import TemplateLookup
from mako.template import Template
from owslib.wms import WebMapService
from sqlalchemy.exc import NoSuchTableError, OperationalError, ProgrammingError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.orm.session import Session
from sqlalchemy.orm.util import class_mapper

import c2cgeoportal_commons.models
import c2cgeoportal_geoportal
from c2cgeoportal_commons.lib.url import Url, get_url2
from c2cgeoportal_geoportal.lib.bashcolor import Color, colorize
from c2cgeoportal_geoportal.lib.caching import init_region
from c2cgeoportal_geoportal.views.layers import Layers, get_layer_class

if TYPE_CHECKING:
    from c2cgeoportal_commons.models import main  # pylint: disable=ungrouped-imports,useless-suppression


class _Registry:
    settings = None

    def __init__(self, settings: Optional[Dict[str, Any]]):
        self.settings = settings


class _Request:
    params: Dict[str, str] = {}
    matchdict: Dict[str, str] = {}
    GET: Dict[str, str] = {}

    def __init__(self, settings: Dict[str, Any] = None):
        self.registry: _Registry = _Registry(settings)

    @staticmethod
    def static_url(*args: Any, **kwargs: Any) -> str:
        del args
        del kwargs
        return ""

    @staticmethod
    def static_path(*args: Any, **kwargs: Any) -> str:
        del args
        del kwargs
        return ""

    @staticmethod
    def route_url(*args: Any, **kwargs: Any) -> str:
        del args
        del kwargs
        return ""

    @staticmethod
    def current_route_url(*args: Any, **kwargs: Any) -> str:
        del args
        del kwargs
        return ""


class GeomapfishAngularExtractor(Extractor):
    """
    GeoMapFish angular extractor
    """

    extensions = [".js", ".html"]

    def __init__(self) -> None:
        super().__init__()
        if os.path.exists("/etc/geomapfish/config.yaml"):
            config.init("/etc/geomapfish/config.yaml")
            self.config = config.get_config()
        else:
            self.config = None
        self.tpl = None

    def __call__(
        self, filename: str, options: Dict[str, Any], fileobj: Dict[str, Any] = None, lineno: int = 0
    ) -> List[Message]:
        del fileobj, lineno

        init_region({"backend": "dogpile.cache.memory"}, "std")
        init_region({"backend": "dogpile.cache.memory"}, "obj")

        int_filename = filename
        if re.match("^" + re.escape("./{}/templates".format(self.config["package"])), filename):
            try:
                empty_template = Template("")  # nosec

                class Lookup(TemplateLookup):
                    @staticmethod
                    def get_template(uri: str) -> Template:
                        del uri  # unused
                        return empty_template

                class MyTemplate(MakoTemplate):
                    tpl = None

                    def prepare(self, **kwargs: Any) -> None:
                        options.update({"input_encoding": self.encoding})
                        lookup = Lookup(**kwargs)
                        if self.source:
                            self.tpl = Template(self.source, lookup=lookup, **kwargs)  # nosec
                        else:
                            self.tpl = Template(  # nosec
                                uri=self.name, filename=self.filename, lookup=lookup, **kwargs
                            )

                try:
                    processed = template(
                        filename,
                        {
                            "request": _Request(self.config),
                            "lang": "fr",
                            "debug": False,
                            "extra_params": {},
                            "permalink_themes": "",
                            "fulltextsearch_groups": [],
                            "wfs_types": [],
                            "_": lambda x: x,
                        },
                        template_adapter=MyTemplate,
                    )
                    int_filename = os.path.join(os.path.dirname(filename), "_" + os.path.basename(filename))
                    with open(int_filename, "wb") as file_open:
                        file_open.write(processed.encode("utf-8"))
                except Exception:
                    print(
                        colorize(
                            "ERROR! Occurred during the '{}' template generation".format(filename), Color.RED
                        )
                    )
                    print(colorize(traceback.format_exc(), Color.RED))
                    if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                        # Continue with the original one
                        int_filename = filename
                    else:
                        raise
            except Exception:
                print(traceback.format_exc())

        message_str = subprocess.check_output(
            ["node", "geoportal/tools/extract-messages.js", int_filename]
        ).decode("utf-8")
        if int_filename != filename:
            os.unlink(int_filename)
        try:
            messages = []
            for contexts, message in json.loads(message_str):
                for context in contexts.split(", "):
                    messages.append(Message(None, message, None, [], "", "", context.split(":")))
            return messages
        except Exception:
            print(colorize("An error occurred", Color.RED))
            print(colorize(message_str, Color.RED))
            print("------")
            raise


class GeomapfishConfigExtractor(Extractor):
    """
    GeoMapFish config extractor (raster layers, and print templates)
    """

    extensions = [".yaml", ".tmpl"]

    def __call__(
        self, filename: str, options: Dict[str, Any], fileobj: Dict[str, Any] = None, lineno: int = 0
    ) -> List[Message]:
        del fileobj, lineno
        init_region({"backend": "dogpile.cache.memory"}, "std")
        init_region({"backend": "dogpile.cache.memory"}, "obj")

        with open(filename) as config_file:
            gmf_config = yaml.load(config_file, Loader=yaml.BaseLoader)  # nosec
            # For application config (config.yaml)
            if "vars" in gmf_config:
                return self._collect_app_config(filename)
            # For the print config
            if "templates" in gmf_config:
                return self._collect_print_config(gmf_config, filename)
            raise Exception("Not a known config file")

    def _collect_app_config(self, filename: str) -> List[Message]:
        config.init(filename)
        settings = config.get_config()
        # Collect raster layers names
        raster = [
            Message(None, raster_layer, None, [], "", "", (filename, "raster/{}".format(raster_layer)))
            for raster_layer in list(settings.get("raster", {}).keys())
        ]

        # Init db sessions

        class R:
            settings: Dict[str, Any] = {}

        class C:
            registry = R()

            def get_settings(self) -> Dict[str, Any]:
                return self.registry.settings

            def add_tween(self, *args: Any, **kwargs: Any) -> None:
                pass

        config_ = C()
        config_.registry.settings = settings

        c2cgeoportal_geoportal.init_dbsessions(settings, config_)

        # Collect layers enum values (for filters)

        from c2cgeoportal_commons.models import DBSessions  # pylint: disable=import-outside-toplevel
        from c2cgeoportal_commons.models.main import Metadata  # pylint: disable=import-outside-toplevel

        enums = []
        enum_layers = settings.get("layers", {}).get("enum", {})
        for layername in list(enum_layers.keys()):
            layerinfos = enum_layers.get(layername, {})
            attributes = layerinfos.get("attributes", {})
            for fieldname in list(attributes.keys()):
                values = self._enumerate_attributes_values(DBSessions, layerinfos, fieldname)
                for (value,) in values:
                    if isinstance(value, str) and value != "":
                        msgid = value
                        location = "/layers/{}/values/{}/{}".format(
                            layername,
                            fieldname,
                            value.encode("ascii", errors="replace").decode("ascii"),
                        )
                        enums.append(Message(None, msgid, None, [], "", "", (filename, location)))

        metadata_list = []
        defs = config["admin_interface"]["available_metadata"]  # pylint: disable=unsubscriptable-object
        names = [e["name"] for e in defs if e.get("translate", False)]

        if names:
            engine = sqlalchemy.create_engine(config["sqlalchemy.url"])
            DBSession = sqlalchemy.orm.session.sessionmaker()  # noqa: disable=N806
            DBSession.configure(bind=engine)
            session = DBSession()

            query = session.query(Metadata).filter(Metadata.name.in_(names))  # pylint: disable=no-member
            for metadata in query.all():
                location = "metadata/{}/{}".format(metadata.name, metadata.id)
                metadata_list.append(Message(None, metadata.value, None, [], "", "", (filename, location)))

        interfaces_messages = []
        for interface, interface_config in config["interfaces_config"].items():
            for ds_index, datasource in enumerate(
                interface_config.get("constants", {}).get("gmfSearchOptions", {}).get("datasources", [])
            ):
                for a_index, action in enumerate(datasource.get("groupActions", [])):
                    location = (
                        "interfaces_config/{}/constants/gmfSearchOptions/datasources[{}]/"
                        "groupActions[{}]/title".format(interface, ds_index, a_index)
                    )
                    interfaces_messages.append(
                        Message(None, action["title"], None, [], "", "", (filename, location))
                    )

            for merge_tab in (
                interface_config.get("constants", {})
                .get("gmfDisplayQueryGridOptions", {})
                .get("mergeTabs", {})
                .keys()
            ):
                location = "interfaces_config/{}/constants/gmfDisplayQueryGridOptions/mergeTabs/{}/".format(
                    interface, merge_tab
                )
                interfaces_messages.append(Message(None, merge_tab, None, [], "", "", (filename, location)))

        return raster + enums + metadata_list + interfaces_messages

    @staticmethod
    def _enumerate_attributes_values(
        dbsessions: Dict[str, Session], layerinfos: Dict[str, Any], fieldname: str
    ) -> List[Message]:
        dbname = layerinfos.get("dbsession", "dbsession")
        translate = cast(Dict[str, Any], layerinfos["attributes"]).get(fieldname, {}).get("translate", True)
        if not translate:
            return []
        try:
            dbsession = dbsessions.get(dbname)
            return Layers.query_enumerate_attribute_values(dbsession, layerinfos, fieldname)
        except Exception as e:
            table = cast(Dict[str, Any], layerinfos["attributes"]).get(fieldname, {}).get("table")
            print(
                colorize(
                    "ERROR! Unable to collect enumerate attributes for "
                    "db: {}, table: {}, column: {} ({})".format(dbname, table, fieldname, e),
                    Color.RED,
                )
            )
            if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                return []
            raise

    @staticmethod
    def _collect_print_config(print_config: Dict[str, Any], filename: str) -> List[Message]:
        result = []
        for template_ in list(cast(Dict[str, Any], print_config.get("templates")).keys()):
            result.append(
                Message(None, template_, None, [], "", "", (filename, "template/{}".format(template_)))
            )
            result += [
                Message(
                    None,
                    attribute,
                    None,
                    [],
                    "",
                    "",
                    (filename, "template/{}/{}".format(template_, attribute)),
                )
                for attribute in list(print_config["templates"][template_]["attributes"].keys())
            ]
        return result


class GeomapfishThemeExtractor(Extractor):
    """
    GeoMapFish theme extractor
    """

    # Run on the development.ini file
    extensions = [".ini"]
    featuretype_cache: Dict[str, Optional[Dict]] = {}
    wmscap_cache: Dict[str, WebMapService] = {}

    def __init__(self) -> None:
        super().__init__()
        if os.path.exists("/etc/geomapfish/config.yaml"):
            config.init("/etc/geomapfish/config.yaml")
            self.config = config.get_config()
        else:
            self.config = None
        self.env = None

    def __call__(
        self, filename: str, options: Dict[str, Any], fileobj: str = None, lineno: int = 0
    ) -> List[Message]:
        del fileobj, lineno
        messages: List[Message] = []

        try:
            engine = sqlalchemy.engine_from_config(self.config, "sqlalchemy_slave.")
            factory = sqlalchemy.orm.sessionmaker(bind=engine)
            db_session = sqlalchemy.orm.scoped_session(factory)
            c2cgeoportal_commons.models.DBSession = db_session
            c2cgeoportal_commons.models.Base.metadata.bind = engine

            try:
                from c2cgeoportal_commons.models.main import (  # pylint: disable=import-outside-toplevel
                    FullTextSearch,
                    LayerGroup,
                    LayerWMS,
                    LayerWMTS,
                    Theme,
                )

                self._import(Theme, messages)
                self._import(LayerGroup, messages)
                self._import(LayerWMS, messages, self._import_layer_wms)
                self._import(LayerWMTS, messages, self._import_layer_wmts)

                for (layer_name,) in db_session.query(FullTextSearch.layer_name).distinct().all():
                    if layer_name is not None and layer_name != "":
                        messages.append(
                            Message(
                                None,
                                layer_name,
                                None,
                                [],
                                "",
                                "",
                                ("fts", layer_name.encode("ascii", errors="replace")),
                            )
                        )

                for (actions,) in db_session.query(FullTextSearch.actions).distinct().all():
                    if actions is not None and actions != "":
                        for action in actions:
                            messages.append(
                                Message(
                                    None,
                                    action["data"],
                                    None,
                                    [],
                                    "",
                                    "",
                                    ("fts", action["data"].encode("ascii", errors="replace")),
                                )
                            )
            except ProgrammingError as e:
                print(
                    colorize(
                        "ERROR! The database is probably not up to date "
                        "(should be ignored when happen during the upgrade)",
                        Color.RED,
                    )
                )
                print(colorize(e, Color.RED))
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                    raise
        except NoSuchTableError as e:
            print(
                colorize(
                    "ERROR! The schema didn't seem to exists "
                    "(should be ignored when happen during the deploy)",
                    Color.RED,
                )
            )
            print(colorize(e, Color.RED))
            if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                raise
        except OperationalError as e:
            print(
                colorize(
                    "ERROR! The database didn't seem to exists "
                    "(should be ignored when happen during the deploy)",
                    Color.RED,
                )
            )
            print(colorize(e, Color.RED))
            if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                raise

        return messages

    @staticmethod
    def _import(
        object_type: Type, messages: List[str], callback: Callable[["main.Layer", List[str]], None] = None
    ) -> None:
        from c2cgeoportal_commons.models import DBSession  # pylint: disable=import-outside-toplevel

        items = DBSession.query(object_type)
        for item in items:
            messages.append(
                Message(
                    None,
                    item.name,
                    None,
                    [],
                    "",
                    "",
                    (item.item_type, item.name.encode("ascii", errors="replace")),
                )
            )

            if callback is not None:
                callback(item, messages)

    def _import_layer_wms(self, layer: "main.Layer", messages: List[str]) -> None:
        server = layer.ogc_server
        url = server.url_wfs or server.url
        if url is None:
            return
        if layer.ogc_server.wfs_support:
            for wms_layer in layer.layer.split(","):
                self._import_layer_attributes(url, wms_layer, layer.item_type, layer.name, messages)
        if layer.geo_table is not None and layer.geo_table != "":
            try:
                cls = get_layer_class(layer, with_last_update_columns=True)
                for column_property in class_mapper(cls).iterate_properties:
                    if isinstance(column_property, ColumnProperty) and len(column_property.columns) == 1:
                        column = column_property.columns[0]
                        if not column.primary_key and not isinstance(column.type, Geometry):
                            if column.foreign_keys:
                                if column.name == "type_id":
                                    name = "type_"
                                elif column.name.endswith("_id"):
                                    name = column.name[:-3]
                                else:
                                    name = column.name + "_"
                            else:
                                name = column_property.key
                            messages.append(
                                Message(
                                    None,
                                    name,
                                    None,
                                    [],
                                    "",
                                    "",
                                    (".".join(["edit", layer.item_type, str(layer.id)]), layer.name),
                                )
                            )
            except NoSuchTableError:
                print(
                    colorize(
                        "ERROR! No such table '{}' for layer '{}'.".format(layer.geo_table, layer.name),
                        Color.RED,
                    )
                )
                print(colorize(traceback.format_exc(), Color.RED))
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                    raise

    def _import_layer_wmts(self, layer: "main.Layer", messages: List[str]) -> None:
        from c2cgeoportal_commons.models import DBSession  # pylint: disable=import-outside-toplevel
        from c2cgeoportal_commons.models.main import OGCServer  # pylint: disable=import-outside-toplevel

        layers = [d.value for d in layer.metadatas if d.name == "queryLayers"]
        if not layers:
            layers = [d.value for d in layer.metadatas if d.name == "wmsLayer"]
        server = [d.value for d in layer.metadatas if d.name == "ogcServer"]
        if server and layers:
            layers = [layer for ls in layers for layer in ls.split(",")]
            for wms_layer in layers:
                try:
                    db_server = DBSession.query(OGCServer).filter(OGCServer.name == server[0]).one()
                    if db_server.wfs_support:
                        self._import_layer_attributes(
                            db_server.url_wfs or db_server.url,
                            wms_layer,
                            layer.item_type,
                            layer.name,
                            messages,
                        )
                except NoResultFound:
                    print(
                        colorize(
                            "ERROR! the OGC server '{}' from the WMTS layer '{}' does not exist.".format(
                                server[0], layer.name
                            ),
                            Color.RED,
                        )
                    )
                    if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                        raise

    def _import_layer_attributes(
        self, url: str, layer: "main.Layer", item_type: str, name: str, messages: List[str]
    ) -> None:
        attributes, layers = self._layer_attributes(url, layer)
        for sub_layer in layers:
            messages.append(
                Message(
                    None,
                    sub_layer,
                    None,
                    [],
                    "",
                    "",
                    (".".join([item_type, name]), sub_layer.encode("ascii", "replace")),
                )
            )
        for attribute in attributes:
            messages.append(
                Message(
                    None,
                    attribute,
                    None,
                    [],
                    "",
                    "",
                    (".".join([item_type, name]), layer.encode("ascii", "replace")),
                )
            )

    def _build_url(self, url: Url) -> Tuple[Url, Dict[str, str], Dict[str, Any]]:
        hostname = url.hostname
        host_map = self.config.get("lingua_extractor", {}).get("host_map", {})
        if hostname in host_map:
            map_ = host_map[hostname]
            if "netloc" in map_:
                url.netloc = map_["netloc"]
            if "scheme" in map_:
                url.scheme = map_["scheme"]
            kwargs = {"verify": map_["verify"]} if "verify" in map_ else {}
            return url, map_.get("headers", {}), kwargs
        return url, {}, {}

    def _layer_attributes(self, url: str, layer: str) -> Tuple[List[str], List[str]]:
        errors: Set[str] = set()

        request = _Request()
        request.registry.settings = self.config
        # Static schema will not be supported
        url_obj_ = get_url2("Layer", url, request, errors)
        if errors:
            print("\n".join(errors))
            return [], []
        if not url_obj_:
            print("No URL for: {}".format(url))
            return [], []
        url_obj: Url = url_obj_
        url_obj, headers, kwargs = self._build_url(url_obj)

        if url not in self.wmscap_cache:
            print("Get WMS GetCapabilities for URL: {}".format(url_obj))
            self.wmscap_cache[url] = None

            wms_getcap_url = (
                url_obj.clone()
                .add_query(
                    {
                        "SERVICE": "WMS",
                        "VERSION": "1.1.1",
                        "REQUEST": "GetCapabilities",
                        "ROLE_IDS": "0",
                        "USER_ID": "0",
                    }
                )
                .url()
            )
            try:
                print(
                    "Get WMS GetCapabilities for URL {},\nwith headers: {}".format(
                        wms_getcap_url, " ".join(["=".join(h) for h in headers.items()])
                    )
                )
                response = requests.get(wms_getcap_url, headers=headers, **kwargs)

                try:
                    self.wmscap_cache[url] = WebMapService(None, xml=response.content)
                except Exception as e:
                    print(
                        colorize(
                            "ERROR! an error occurred while trying to " "parse the GetCapabilities document.",
                            Color.RED,
                        )
                    )
                    print(colorize(str(e), Color.RED))
                    print("URL: {}\nxml:\n{}".format(wms_getcap_url, response.text))
                    if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                        raise
            except Exception as e:
                print(colorize(str(e), Color.RED))
                print(
                    colorize(
                        "ERROR! Unable to GetCapabilities from URL: {},\nwith headers: {}".format(
                            wms_getcap_url, " ".join(["=".join(h) for h in headers.items()])
                        ),
                        Color.RED,
                    )
                )
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") != "TRUE":
                    raise

        wmscap = self.wmscap_cache[url]

        if url not in self.featuretype_cache:
            print("Get WFS DescribeFeatureType for URL: {}".format(url_obj))
            self.featuretype_cache[url] = None

            wfs_descrfeat_url = (
                url_obj.clone()
                .add_query(
                    {
                        "SERVICE": "WFS",
                        "VERSION": "1.1.0",
                        "REQUEST": "DescribeFeatureType",
                        "ROLE_IDS": "0",
                        "USER_ID": "0",
                    }
                )
                .url()
            )
            try:
                response = requests.get(wfs_descrfeat_url, headers=headers, **kwargs)
            except Exception as e:
                print(colorize(str(e), Color.RED))
                print(
                    colorize(
                        "ERROR! Unable to DescribeFeatureType from URL: {}".format(wfs_descrfeat_url),
                        Color.RED,
                    )
                )
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                    return [], []
                raise

            if not response.ok:
                print(
                    colorize(
                        "ERROR! DescribeFeatureType from URL {} return the error: {:d} {}".format(
                            wfs_descrfeat_url, response.status_code, response.reason
                        ),
                        Color.RED,
                    )
                )
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                    return [], []
                raise Exception("Aborted")

            try:
                describe = parseString(response.text)
                featurestype: Optional[Dict[str, Node]] = {}
                self.featuretype_cache[url] = featurestype
                for type_element in describe.getElementsByTagNameNS(
                    "http://www.w3.org/2001/XMLSchema", "complexType"
                ):
                    cast(Dict[str, Node], featurestype)[type_element.getAttribute("name")] = type_element
            except ExpatError as e:
                print(
                    colorize(
                        "ERROR! an error occurred while trying to " "parse the DescribeFeatureType document.",
                        Color.RED,
                    )
                )
                print(colorize(str(e), Color.RED))
                print("URL: {}\nxml:\n{}".format(wfs_descrfeat_url, response.text))
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                    return [], []
                raise
            except AttributeError:
                print(
                    colorize(
                        "ERROR! an error occurred while trying to "
                        "read the Mapfile and recover the themes.",
                        Color.RED,
                    )
                )
                print("URL: {}\nxml:\n{}".format(wfs_descrfeat_url, response.text))
                if os.environ.get("IGNORE_I18N_ERRORS", "FALSE") == "TRUE":
                    return [], []
                raise
        else:
            featurestype = self.featuretype_cache[url]

        if featurestype is None:
            return [], []

        layers: List[str] = [layer]
        if wmscap is not None and layer in list(wmscap.contents):
            layer_obj = wmscap[layer]
            if layer_obj.layers:
                layers = [layer.name for layer in layer_obj.layers]

        attributes: List[str] = []
        for sub_layer in layers:
            # Should probably be adapted for other king of servers
            type_element = featurestype.get("{}Type".format(sub_layer))
            if type_element is not None:
                for element in type_element.getElementsByTagNameNS(
                    "http://www.w3.org/2001/XMLSchema", "element"
                ):
                    if not element.getAttribute("type").startswith("gml:"):
                        attributes.append(element.getAttribute("name"))

        return attributes, layers
