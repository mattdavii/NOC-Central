"""Validated location metadata with legacy lat/lon compatibility."""
import math
from datetime import datetime, timezone


def parse_location(data, default_source='manual'):
    lat, lon = data.get('latitude', data.get('lat')), data.get('longitude', data.get('lon'))
    if lat in (None, '') and lon in (None, ''):
        return dict(latitude=None, longitude=None, accuracy_m=None, location_source=None, location_updated_at=None)
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise ValueError()
        lat, lon = float(lat), float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError()
        accuracy = data.get('accuracy_m')
        accuracy = None if accuracy in (None, '') else float(accuracy)
        if accuracy is not None and (not math.isfinite(accuracy) or accuracy < 0):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Coordenadas ou precisão inválidas.') from None
    source = data.get('location_source') or default_source
    if source not in ('browser', 'manual', 'ip', 'legacy'):
        raise ValueError('Fonte de localização inválida.')
    if source == 'browser' and accuracy is None:
        raise ValueError('Captura do navegador deve informar precisão em metros.')
    if source in ('manual', 'legacy', 'ip'):
        accuracy = None
    return dict(latitude=lat, longitude=lon, accuracy_m=accuracy, location_source=source,
                location_updated_at=datetime.now(timezone.utc).isoformat())


def save_location(conn, mac, location, protect_precise=False):
    sql = '''UPDATE sensores SET latitude=?, longitude=?, lat=?, lon=?, accuracy_m=?,
        location_source=?, location_updated_at=? WHERE mac_id=?'''
    if protect_precise:
        sql += " AND (location_source IS NULL OR location_source IN ('ip', 'legacy'))"
    conn.execute(sql, (location['latitude'], location['longitude'], location['latitude'], location['longitude'],
        location['accuracy_m'], location['location_source'], location['location_updated_at'], mac))
