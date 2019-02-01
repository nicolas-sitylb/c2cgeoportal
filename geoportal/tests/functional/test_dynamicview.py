# -*- coding: utf-8 -*-

# Copyright (c) 2018-2019, Camptocamp SA
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

# pylint: disable=missing-docstring,attribute-defined-outside-init,protected-access


from pyramid import testing
from unittest import TestCase
from pyramid.testing import DummyRequest

from tests.functional import (  # noqa, pylint: disable=unused-import
    teardown_common as teardown_module,
    setup_common as setup_module
)


class TestEchoView(TestCase):

    @staticmethod
    def setup_method(_):
        import transaction
        from sqlalchemy import func
        from geoalchemy2 import WKTElement
        from c2cgeoportal_commons.models import DBSession
        from c2cgeoportal_commons.models.main import FullTextSearch

        entry1 = FullTextSearch()
        entry1.label = "label 1"
        entry1.layer_name = "layer1"
        entry1.ts = func.to_tsvector("french", "soleil travail")
        entry1.the_geom = WKTElement("POINT(-90 -45)", 21781)
        entry1.public = True

        entry2 = FullTextSearch()
        entry2.label = "label 2"
        entry2.layer_name = "layer2"
        entry2.ts = func.to_tsvector("french", "pluie semaine")
        entry2.the_geom = WKTElement("POINT(-90 -45)", 21781)
        entry1.public = True

        entry3 = FullTextSearch()
        entry3.label = "label 3"
        entry3.layer_name = "layer2"
        entry3.ts = func.to_tsvector("french", "vent neige")
        entry3.the_geom = WKTElement("POINT(-90 -45)", 21781)
        entry1.public = True

        DBSession.add_all([entry1, entry2, entry3])
        transaction.commit()

    @staticmethod
    def teardown_method(_):
        testing.tearDown()

        import transaction
        from c2cgeoportal_commons.models import DBSession
        from c2cgeoportal_commons.models.main import FullTextSearch

        DBSession.query(FullTextSearch).filter(FullTextSearch.label == "label 1").delete()
        DBSession.query(FullTextSearch).filter(FullTextSearch.label == "label 2").delete()
        DBSession.query(FullTextSearch).filter(FullTextSearch.label == "label 3").delete()

        transaction.commit()

    @staticmethod
    def _get_settings(settings):
        return {
            'interfaces': ['test'],
            'available_locale_names': ['fr'],
            'package': 'package_name',
            'interfaces_config': settings,
        }

    @staticmethod
    def _request(query={}):
        query_ = {'interface': 'test'}
        query_.update(query)
        request = DummyRequest(query_)
        request.route_url = lambda url, _query=None: "/dummy/route/url/{}".format(url) if _query is None \
            else "/dummy/route/url/{}?{}".format(url, '&'.join(['='.join(e) for e in _query.items()]))
        request.static_url = lambda url: "/dummy/static/url/" + url
        return request

    def test_constant(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'constants': {
                    'XTest': 'TOTO'
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == 'TOTO'

    def test_constant_default(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'default': {
                'constants': {
                    'XTest': 'TOTO'
                }
            },
            'test': {}
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == 'TOTO'

    def test_constant_json(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'constants': {
                    'XTest': ['TOTO']
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == ['TOTO']

    def test_constant_dynamic_interface(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'dynamic_constants': {
                    'XTest': 'interface'
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == 'test'

    def test_constant_dynamic_cache_version(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'dynamic_constants': {
                    'XTest': 'cache_version'
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        from c2cgeoportal_geoportal.lib.cacheversion import get_cache_version
        assert dynamic['constants']['XTest'] == get_cache_version()

    def test_constant_dynamic_lang_urls(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.static_url = lambda url: "/dummy/route/url/" + url
        request.registry.settings = self._get_settings({
            'test': {
                'dynamic_constants': {
                    'XTest': 'lang_urls'
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == {
            'fr': '/dummy/route/url/package_name_geoportal:static-ngeo/build/fr.json'
        }

    def test_constant_dynamic_fulltextsearch_groups(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.static_url = lambda url: "/dummy/route/url/" + url
        request.registry.settings = self._get_settings({
            'test': {
                'dynamic_constants': {
                    'XTest': 'fulltextsearch_groups'
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == ['layer1', 'layer2']

    def test_static(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'static': {
                    'XTest': {
                        'name': 'test',
                        'append': '/{{name}}.yaml'
                    }
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == '/dummy/static/url/test/{{name}}.yaml'

    def test_route(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'routes': {
                    'XTest': {
                        'name': 'test',
                        'params': {
                            'test': 'value'
                        }
                    }
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == '/dummy/route/url/test?test=value'

    def test_route_dyamic(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'routes': {
                    'XTest': {
                        'name': 'test',
                        'dynamic_params': {
                            'test': 'interface'
                        }
                    }
                }
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert 'XTest' in dynamic['constants'], dynamic
        assert dynamic['constants']['XTest'] == '/dummy/route/url/test?test=test'

    def test_redirect(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'redirect_interface': 'test_redirect'
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert dynamic == {
            'constants': {
                'currentInterfaceUrl': '/dummy/route/url/test?',
                'redirectUrl': '/dummy/route/url/test_redirect?no_redirect=t',
            },
            'doRedirect': False,
            'redirectUrl': '/dummy/route/url/test_redirect?',
        }

    def test_doredirect(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request()
        request.registry.settings = self._get_settings({
            'test': {
                'redirect_interface': 'test_redirect',
                'do_redirect': True
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert dynamic == {
            'constants': {
                'currentInterfaceUrl': '/dummy/route/url/test?',
            },
            'doRedirect': True,
            'redirectUrl': '/dummy/route/url/test_redirect?',
        }

    def test_noredirect(self):
        from c2cgeoportal_geoportal.views.dynamic import DynamicView
        request = self._request({'no_redirect': 't'})
        request.registry.settings = self._get_settings({
            'test': {
                'redirect_interface': 'test_redirect',
                'do_redirect': True
            }
        })
        dynamic = DynamicView(request).dynamic()

        assert dynamic == {
            'constants': {
                'currentInterfaceUrl': '/dummy/route/url/test?',
            },
            'doRedirect': True,
            'redirectUrl': '/dummy/route/url/test_redirect?',
        }
