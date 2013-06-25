#!/usr/bin/python

from charmhelpers.contrib.openstack import (
    templating,
    context,
    )

from collections import OrderedDict

import subprocess

CHARM = "glance"

SERVICES = "glance-api glance-registry"
PACKAGES = "glance python-mysqldb python-swift python-keystone uuid haproxy"

GLANCE_REGISTRY_CONF = "/etc/glance/glance-registry.conf"
GLANCE_REGISTRY_PASTE_INI = "/etc/glance/glance-registry-paste.ini"
GLANCE_API_CONF = "/etc/glance/glance-api.conf"
GLANCE_API_PASTE_INI = "/etc/glance/glance-api-paste.ini"
CONF_DIR = "/etc/glance"

# Flag used to track config changes.
CONFIG_CHANGED =  False

TEMPLATES = 'templates/'

CONFIG_FILES = OrderedDict([
    ('/etc/glance/glance-registry.conf', {
        'hook_contexts': [context.SharedDBContext(),
                          context.IdentityServiceContext()],
        'services': ['glance-registry']
    }),
    ('/etc/glance/glance-api.conf', {
        'hook_contexts': [context.SharedDBContext(),
                          context.IdentityServiceContext()],
        'services': ['glance-api']
    }),
    ('/etc/glance/glance-api-paste.ini', {
        'hook_contexts': [context.IdentityServiceContext()],
        'services': ['glance-api']
    }),
    ('/etc/glance/glance-registry-paste.ini', {
        'hook_contexts': [context.IdentityServiceContext()],
        'services': ['glance-registry']
    }),
    ('/etc/ceph/ceph.conf', {
        'hook_contexts': [context.CephContext()],
        'services': []
    }),
])

def register_configs():
    # Register config files with their respective contexts.
    # Regstration of some configs may not be required depending on
    # existing of certain relations.
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release='grizzly')

    confs = ['/etc/glance/glance-registry.conf',
             '/etc/glance/glance-api.conf',
             '/etc/glance/glance-api-paste.ini',
             '/etc/glance/glance-registry-paste.ini',]

    if relation_ids('ceph'):
        confs.append('/etc/ceph/ceph.conf')

    for conf in confs:
        configs.register(conf, CONFIG_FILES[conf]['hook_contexts'])

    return configs


def migrate_database():
    '''Runs glance-manage to initialize a new database or migrate existing'''
    cmd = ['glance-manage', 'db_sync']
    subprocess.check_call(cmd)
