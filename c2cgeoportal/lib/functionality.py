# -*- coding: utf-8 -*-

import simplejson as json

from pyramid.security import authenticated_userid

from c2cgeoportal.models import DBSession, User


def _cleanup(value, config):
    result = config[value]
    start = result.index('{')
    end = result.rindex('}')
    result = result[start:end + 1]
    return result.replace('\\n', '\n')


def _get_json_functionalities(value, name, config):
    obj = json.loads(_cleanup(value, config))
    return obj[name] if name in obj else None


def _get_config_functionalities(name, registered, config):
    result = _get_json_functionalities('registered_functionalities',
            name, config)
    return result if result != None and registered \
        else _get_json_functionalities('anonymous_functionalities',
            name, config)


def _get_db_functionalities(name, request):
    username = authenticated_userid(request)
    user = None if username is None \
            else DBSession.query(User).filter_by(username=username).one()

    result = []
    if user:
        result = [functionality.value for functionality in user.functionalities if functionality.name == name]
        if len(result) == 0:
            result = [functionality.value for functionality in user.role.functionalities if functionality.name == name]
    return (result, user != None)


def get_functionality(name, config, request, default=None):
    result, registred = _get_db_functionalities(name, request)
    if len(result) == 0:
        return _get_config_functionalities(name, registred, config) or default
    else:
        return result[0]


def get_functionalities(name, config, request, default=[]):
    result, registred = _get_db_functionalities(name, request)
    if len(result) == 0:
        return _get_config_functionalities(name, registred, config) or default
    else:
        return result
