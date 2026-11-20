from archive_store import store_identity
from custody.catalogue import catalogue_write
from media.volume import CopyIdentity, VolumeIdentity, copy_fields, volume_fields, volume_settings


def register_volume_domain(catalogue, volume):
    settings = volume_settings(volume)
    if settings.catalogue_id != store_identity(catalogue, 'catalogue'):
        raise ValueError('volume belongs to a different catalogue')
    site, name, domain = volume_fields(settings.identity)
    with catalogue_write(catalogue):
        catalogue.execute('''
            INSERT INTO registered_volumes VALUES (?, ?, ?, ?)
            ON CONFLICT(site, volume) DO NOTHING
        ''', (site, name, domain, settings.permit_key))
        if registered_key(catalogue, settings.identity) != settings.permit_key:
            raise ValueError('registered volume key conflicts with the local binding')
    return settings.identity


def _registration(catalogue, identity):
    site, volume, domain = volume_fields(identity)
    found = catalogue.execute('''
        SELECT domain, permit_key FROM registered_volumes WHERE site=? AND volume=?
    ''', (site, volume)).fetchone()
    if found is None:
        raise KeyError(identity)
    if found[0] != domain:
        raise ValueError('registered failure domain does not match the volume')
    return found


def registered_domain(catalogue, identity):
    return _registration(catalogue, identity)[0]


def registered_key(catalogue, identity):
    return _registration(catalogue, identity)[1]


def record_acceptance(catalogue, copy):
    site, volume, _, batch_id, copy_id = copy_fields(copy)
    registered_domain(catalogue, copy.volume)
    catalogue.execute('''
        INSERT INTO verified_copies VALUES (?, ?, ?, ?)
        ON CONFLICT(batch_id, site, volume, copy_id) DO NOTHING
    ''', (batch_id, site, volume, copy_id))
    return copy


def list_verified_copies(catalogue, batch_id):
    rows = catalogue.execute('''
        SELECT v.site, v.volume, r.domain, v.copy_id
        FROM verified_copies AS v JOIN registered_volumes AS r USING(site, volume)
        WHERE v.batch_id=? ORDER BY v.site, v.volume, v.copy_id
    ''', (batch_id,))
    return tuple(CopyIdentity(VolumeIdentity(site, volume, domain), batch_id, copy_id)
                 for site, volume, domain, copy_id in rows)


def record_retire_exclusion(catalogue, copy, request_id):
    site, volume, _, batch_id, copy_id = copy_fields(copy)
    catalogue.execute('INSERT INTO retire_exclusions VALUES (?, ?, ?, ?, ?)',
                      (batch_id, site, volume, copy_id, request_id))


def list_retire_exclusions(catalogue, batch_id):
    rows = catalogue.execute('''
        SELECT e.site, e.volume, r.domain, e.copy_id
        FROM retire_exclusions AS e JOIN registered_volumes AS r USING(site, volume)
        WHERE e.batch_id=?
    ''', (batch_id,))
    return frozenset(CopyIdentity(VolumeIdentity(site, volume, domain), batch_id, copy_id)
                     for site, volume, domain, copy_id in rows)
