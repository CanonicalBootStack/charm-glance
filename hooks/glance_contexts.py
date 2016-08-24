# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from charmhelpers.core.hookenv import (
    is_relation_made,
    relation_ids,
    service_name,
    config
)

from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
    ApacheSSLContext as SSLContext,
    BindHostContext
)

from charmhelpers.contrib.hahelpers.cluster import (
    determine_apache_port,
    determine_api_port,
)

from charmhelpers.contrib.openstack.utils import (
    os_release
)


class CephGlanceContext(OSContextGenerator):
    interfaces = ['ceph-glance']

    def __call__(self):
        """Used to generate template context to be added to glance-api.conf in
        the presence of a ceph relation.
        """
        if not is_relation_made(relation="ceph",
                                keys="key"):
            return {}
        service = service_name()
        return {
            # pool created based on service name.
            'rbd_pool': service,
            'rbd_user': service,
            'expose_image_locations': config('expose-image-locations')
        }


class ObjectStoreContext(OSContextGenerator):
    interfaces = ['object-store']

    def __call__(self):
        """Object store config.
        Used to generate template context to be added to glance-api.conf in
        the presence of a 'object-store' relation.
        """
        if not relation_ids('object-store'):
            return {}
        return {
            'swift_store': True,
        }


class CinderStoreContext(OSContextGenerator):
    interfaces = ['cinder-volume-service']

    def __call__(self):
        """Cinder store config.
        Used to generate template context to be added to glance-api.conf in
        the presence of a 'cinder-volume-service' relation.
        """
        if not relation_ids('cinder-volume-service'):
            return {}
        return {
            'cinder_store': True,
        }


class MultiStoreContext(OSContextGenerator):

    def __call__(self):
        stores = ['glance.store.filesystem.Store', 'glance.store.http.Store']
        store_mapping = {
            'ceph': 'glance.store.rbd.Store',
            'object-store': 'glance.store.swift.Store',
        }
        for store_relation, store_type in store_mapping.iteritems():
            if relation_ids(store_relation):
                stores.append(store_type)
        if (relation_ids('cinder-volume-service') and
                os_release('glance-common') >= 'mitaka'):
            stores.append('glance.store.cinder.Store')
        return {
            'known_stores': ','.join(stores)
        }


class HAProxyContext(OSContextGenerator):
    interfaces = ['cluster']

    def __call__(self):
        '''Extends the main charmhelpers HAProxyContext with a port mapping
        specific to this charm.
        Also used to extend glance-api.conf context with correct bind_port
        '''
        haproxy_port = 9292
        apache_port = determine_apache_port(9292, singlenode_mode=True)
        api_port = determine_api_port(9292, singlenode_mode=True)

        ctxt = {
            'service_ports': {'glance_api': [haproxy_port, apache_port]},
            'bind_port': api_port,
        }
        return ctxt


class ApacheSSLContext(SSLContext):
    interfaces = ['https']
    external_ports = [9292]
    service_namespace = 'glance'

    def __call__(self):
        return super(ApacheSSLContext, self).__call__()


class LoggingConfigContext(OSContextGenerator):

    def __call__(self):
        return {'debug': config('debug'), 'verbose': config('verbose')}


class GlanceIPv6Context(BindHostContext):

    def __call__(self):
        ctxt = super(GlanceIPv6Context, self).__call__()
        if config('prefer-ipv6'):
            ctxt['registry_host'] = '[::]'
        else:
            ctxt['registry_host'] = '0.0.0.0'

        return ctxt
