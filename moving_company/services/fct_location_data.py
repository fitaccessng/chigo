FCT_AREA_COUNCILS = (
    'Abuja Municipal Area Council',
    'Bwari Area Council',
    'Gwagwalada Area Council',
    'Kuje Area Council',
    'Kwali Area Council',
    'Abaji Area Council',
)


def _place(name, place_type, council, parent, latitude, longitude, *, district=None,
           neighborhood=None, aliases=(), keywords=(), popularity=0, address=None,
           verified=True, verification_status=None, coordinate_precision='area',
           source='chigo-curated', source_attributes=None):
    return {
        'name': name,
        'place_type': place_type,
        'area_council': council,
        'parent_name': parent,
        'district': district,
        'neighborhood': neighborhood,
        'area': neighborhood or district or name,
        'latitude': latitude,
        'longitude': longitude,
        'aliases': list(aliases),
        'search_keywords': list(keywords),
        'popularity_score': popularity,
        'address': address or f'{name}, {council}, Federal Capital Territory, Nigeria',
        'state': 'Federal Capital Territory',
        'country': 'Nigeria',
        'provider': source,
        'verified': verified,
        'verification_status': verification_status or ('verified' if verified else 'source_listed'),
        'coordinate_precision': coordinate_precision,
        'source_attributes': source_attributes or {},
        'active': True,
    }


def _listed_place(
    name, council, zone, parent=None, *,
    place_type='neighborhood', aliases=(), boundary_status='listed',
):
    parent = parent or council
    district = parent if parent != council else None
    return _place(
        name, place_type, council, parent, None, None,
        district=district,
        neighborhood=name if place_type == 'neighborhood' else None,
        aliases=aliases,
        address=f'{name}, {zone}, {council}, Federal Capital Territory, Nigeria',
        verified=False,
        verification_status='needs_review' if boundary_status == 'needs_review' else 'source_listed',
        coordinate_precision='unknown',
        source='chigo-user-provided-directory',
        source_attributes={
            'directory_zone': zone,
            'coordinate_status': 'not_geocoded',
            'boundary_status': boundary_status,
        },
    )


FCT_LOCATION_SEED = [
    _place('Abuja Municipal Area Council', 'area_council', 'Abuja Municipal Area Council', None, 9.0578, 7.4951, aliases=('AMAC',), popularity=100),
    _place('Bwari Area Council', 'area_council', 'Bwari Area Council', None, 9.2813, 7.3736, aliases=('Bwari',), popularity=78),
    _place('Gwagwalada Area Council', 'area_council', 'Gwagwalada Area Council', None, 8.9435, 7.0913, aliases=('Gwagwalada',), popularity=74),
    _place('Kuje Area Council', 'area_council', 'Kuje Area Council', None, 8.8795, 7.2276, aliases=('Kuje',), popularity=70),
    _place('Kwali Area Council', 'area_council', 'Kwali Area Council', None, 8.8217, 6.9835, aliases=('Kwali',), popularity=56),
    _place('Abaji Area Council', 'area_council', 'Abaji Area Council', None, 8.4759, 6.9447, aliases=('Abaji',), popularity=52),
    _place('Abuja Central', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0570, 7.4950, aliases=('Central Business District', 'CBD', 'Central Area'), popularity=98),
    _place('Garki', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0360, 7.4890, popularity=91),
    _place('Wuse', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0650, 7.4730, aliases=('Wuse 1', 'Wuse I', 'Wuse II', 'Wuse 2', 'Wuse Two'), popularity=95),
    _place('Maitama', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0880, 7.4910, popularity=94),
    _place('Asokoro', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0330, 7.5360, popularity=88),
    _place('Gwarinpa', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.1180, 7.4230, aliases=('Gwarimpa',), popularity=93),
    _place('Jabi', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0720, 7.4180, popularity=90),
    _place('Utako', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0720, 7.4320, popularity=82),
    _place('Kado', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.1050, 7.4380, popularity=79),
    _place('Life Camp', 'neighborhood', 'Abuja Municipal Area Council', 'Gwarinpa', 9.1180, 7.3890, district='Gwarinpa', aliases=('Lifecamp', 'Life-Camp'), popularity=89),
    _place('Lugbe', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 8.9730, 7.3830, popularity=86),
    _place('Jikwoyi', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0720, 7.4200, aliases=('Jikoyi', 'Jikowyi'), popularity=78),
    _place('Karu', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0200, 7.5680, aliases=('Karu Site',), popularity=82),
    _place('Nyanya', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 9.0380, 7.5650, popularity=80),
    _place('Galadimawa', 'district', 'Abuja Municipal Area Council', 'Abuja Municipal Area Council', 8.9950, 7.4920, popularity=72),
    _place('Kubwa', 'district', 'Bwari Area Council', 'Bwari Area Council', 9.1530, 7.3230, aliases=('Kubwa Abuja',), popularity=92),
    _place('Dutse', 'district', 'Bwari Area Council', 'Bwari Area Council', 9.2250, 7.3500, popularity=68),
    _place('Bwari', 'district', 'Bwari Area Council', 'Bwari Area Council', 9.2810, 7.3730, popularity=78),
    _place('Zuba', 'district', 'Gwagwalada Area Council', 'Gwagwalada Area Council', 9.1020, 7.2060, popularity=80),
    _place('Gwagwalada', 'district', 'Gwagwalada Area Council', 'Gwagwalada Area Council', 8.9430, 7.0910, popularity=87),
    _place('Kuje', 'district', 'Kuje Area Council', 'Kuje Area Council', 8.8800, 7.2280, popularity=82),
    _place('Kwali', 'district', 'Kwali Area Council', 'Kwali Area Council', 8.8220, 6.9840, popularity=61),
    _place('Abaji', 'district', 'Abaji Area Council', 'Abaji Area Council', 8.4760, 6.9450, popularity=58),
    _place('Gwarinpa Estate', 'estate', 'Abuja Municipal Area Council', 'Gwarinpa', 9.1240, 7.4140, district='Gwarinpa', aliases=('Gwarinpa 1st Avenue', 'Gwarinpa 2nd Avenue', 'Gwarinpa Estate Abuja'), popularity=85),
    _place('Citec Mbora Estate', 'estate', 'Abuja Municipal Area Council', 'Kado', 9.1380, 7.4390, district='Kado', aliases=('Citec Estate', 'Mbora Estate'), popularity=69),
    _place('Sun City Estate', 'estate', 'Abuja Municipal Area Council', 'Galadimawa', 8.9980, 7.4930, district='Galadimawa', popularity=64),
    _place('River Park Estate', 'estate', 'Abuja Municipal Area Council', 'Lugbe', 8.9700, 7.3650, district='Lugbe', aliases=('River Park Abuja',), popularity=68),
    _place('FHA Lugbe Estate', 'estate', 'Abuja Municipal Area Council', 'Lugbe', 8.9670, 7.3770, district='Lugbe', aliases=('FHA Lugbe',), popularity=67),
    _place('Nnamdi Azikiwe International Airport', 'airport', 'Kuje Area Council', 'Kuje Area Council', 9.0068, 7.2632, aliases=('Abuja Airport', 'Abuja International Airport', 'Nnamdi Azikiwe Airport', 'Airport Abuja'), keywords=('airport', 'air terminal', 'flight'), popularity=100),
    _place('Jabi Lake Mall', 'shopping_centre', 'Abuja Municipal Area Council', 'Jabi', 9.0720, 7.4270, aliases=('Jabi Mall',), keywords=('shopping', 'mall', 'shoprite'), popularity=90),
    _place('Central Area National Mosque', 'landmark', 'Abuja Municipal Area Council', 'Abuja Central', 9.0640, 7.4900, aliases=('National Mosque Abuja',), popularity=87),
    _place('National Assembly Complex', 'commercial', 'Abuja Municipal Area Council', 'Abuja Central', 9.0470, 7.4890, aliases=('National Assembly', 'NASS'), popularity=85),
    _place('National Hospital Abuja', 'hospital', 'Abuja Municipal Area Council', 'Garki', 9.0360, 7.4930, aliases=('National Hospital',), popularity=86),
    _place('University of Abuja Main Campus', 'university', 'Gwagwalada Area Council', 'Gwagwalada', 8.9440, 7.1000, aliases=('UniAbuja', 'University of Abuja'), popularity=89),
    _place('University of Abuja Teaching Hospital', 'hospital', 'Gwagwalada Area Council', 'Gwagwalada', 8.9440, 7.0800, aliases=('UATH',), popularity=78),
    _place('Transcorp Hilton Abuja', 'hotel', 'Abuja Municipal Area Council', 'Maitama', 9.0780, 7.4890, aliases=('Hilton Abuja', 'Transcorp Hilton'), popularity=89),
    _place('Abuja Continental Hotel', 'hotel', 'Abuja Municipal Area Council', 'Wuse', 9.0600, 7.4780, aliases=('Sheraton Abuja', 'Abuja Continental'), popularity=84),
    _place('Kubwa Expressway', 'road', 'Bwari Area Council', 'Kubwa', 9.1300, 7.3600, aliases=('Kubwa Express', 'Abuja-Kubwa Road', 'Kubwa expressway corridor'), keywords=('highway', 'express road'), popularity=75),
    _place('Airport Road Abuja', 'road', 'Abuja Municipal Area Council', 'Lugbe', 8.9900, 7.3900, aliases=('Nnamdi Azikiwe Expressway', 'Umaru Musa Yar’Adua Expressway'), keywords=('highway', 'airport road'), popularity=83),
    _place('Ahmadu Bello Way', 'road', 'Abuja Municipal Area Council', 'Garki', 9.0450, 7.4850, aliases=('AB Way',), popularity=73),
    _place('Murtala Mohammed Way', 'road', 'Abuja Municipal Area Council', 'Abuja Central', 9.0580, 7.4780, aliases=('Murtala Muhammad Way',), popularity=71),
]

_ABUJA_DIRECTORY_PLACES = (
    # AMAC: central districts and neighbourhoods
    ('Garki I', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Garki II', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Wuse I', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Wuse', 'neighborhood', ('Wuse 1',)),
    ('Wuse II', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Wuse', 'neighborhood', ('Wuse 2', 'Wuse Two')),
    ('Guzape', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Gudu', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Jahi', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Katampe', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Katampe Extension', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Katampe', 'neighborhood', ('Katampe Ext',)),
    ('Mabushi', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Durumi', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Abuja Municipal Area Council', 'district', ()),
    ('Area 1', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 2', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 3', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 7', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 8', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 10', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),
    ('Area 11', 'Abuja Municipal Area Council', 'City Centre / FCC', 'Garki', 'neighborhood', ()),

    # AMAC: airport, southern and eastern neighbourhoods
    ('Pyakasa', 'Abuja Municipal Area Council', 'Airport / South / East', 'Lugbe', 'neighborhood', ()),
    ('Sauka', 'Abuja Municipal Area Council', 'Airport / South / East', 'Lugbe', 'neighborhood', ()),
    ('Kuchigoro', 'Abuja Municipal Area Council', 'Airport / South / East', 'Lugbe', 'neighborhood', ()),
    ('Apo', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Apo Resettlement', 'Abuja Municipal Area Council', 'Airport / South / East', 'Apo', 'neighborhood', ()),
    ('Lokogoma', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Gaduwa', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Dakibiyu', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Kurudu', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Karshi', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),
    ('Kpeyegyi', 'Abuja Municipal Area Council', 'Airport / South / East', 'Abuja Municipal Area Council', 'district', ()),

    # AMAC: additional settlements. Boundary-uncertain assignments are intentionally omitted.
    ('Kugbo', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ('Kugbo Village',)),
    ('Karmo', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),
    ('Wuye', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),
    ('Kaura', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),
    ('Kabusa', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),
    ('Chika', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),
    ('Idu', 'Abuja Municipal Area Council', 'Other AMAC / satellite', 'Abuja Municipal Area Council', 'district', ()),

    # Bwari
    ('Dutse Alhaji', 'Bwari Area Council', 'Bwari / north-west', 'Dutse', 'neighborhood', ()),
    ('Dawaki', 'Bwari Area Council', 'Bwari / north-west', 'Bwari Area Council', 'district', ()),
    ('Ushafa', 'Bwari Area Council', 'Bwari / north-west', 'Bwari Area Council', 'district', ()),
    ('Kubwa Extension', 'Bwari Area Council', 'Bwari / north-west', 'Kubwa', 'neighborhood', ()),
    ('Byazhin', 'Bwari Area Council', 'Bwari / north-west', 'Kubwa', 'neighborhood', ()),
    ('Kubwa Village', 'Bwari Area Council', 'Bwari / north-west', 'Kubwa', 'neighborhood', ()),
    ('Igu', 'Bwari Area Council', 'Bwari / north-west', 'Bwari Area Council', 'village', ()),
    ('Kawu', 'Bwari Area Council', 'Bwari / north-west', 'Bwari Area Council', 'village', ()),
    ('Shere', 'Bwari Area Council', 'Bwari / north-west', 'Bwari Area Council', 'village', ()),
    ('Jiwa', 'Bwari Area Council', 'Other settlements', 'Bwari Area Council', 'village', ()),
    ('Kagini', 'Bwari Area Council', 'Other settlements', 'Bwari Area Council', 'village', ()),
    ('Dakwa', 'Bwari Area Council', 'Other settlements', 'Bwari Area Council', 'village', ()),
    ('Tungan Maje', 'Bwari Area Council', 'Other settlements', 'Bwari Area Council', 'village', ('Tunga Maje',)),
    ('Dei-Dei', 'Bwari Area Council', 'Other settlements', 'Bwari Area Council', 'district', ()),

    # Gwagwalada
    ('Dobi', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Gwako', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Ibwa', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Paiko', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Kutunku', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'neighborhood', ()),
    ('Giri', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Anagada', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Ikwa', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Dukwa', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada Area Council', 'village', ()),
    ('Gwagwalada town centre', 'Gwagwalada Area Council', 'Gwagwalada axis', 'Gwagwalada', 'neighborhood', ('Gwagwalada town center',)),

    # Kuje
    ('Chibiri', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Rubochi', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Kwaku', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Gudun Karya', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Gaube', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Kabi', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'village', ()),
    ('Kuje town centre', 'Kuje Area Council', 'Kuje axis', 'Kuje', 'neighborhood', ('Kuje town center',)),
    ('Kuje Airport corridor', 'Kuje Area Council', 'Kuje axis', 'Kuje Area Council', 'road', ('Kuje Airport Road',)),

    # Kwali
    ('Pai', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Yangoji', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Kilankwa', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Dafa', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Wako', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Ashara', 'Kwali Area Council', 'Kwali axis', 'Kwali Area Council', 'village', ()),
    ('Kwali town centre', 'Kwali Area Council', 'Kwali axis', 'Kwali', 'neighborhood', ('Kwali town center',)),

    # Abaji
    ('Nuku', 'Abaji Area Council', 'Abaji axis', 'Abaji Area Council', 'village', ()),
    ('Gawu', 'Abaji Area Council', 'Abaji axis', 'Abaji Area Council', 'village', ()),
    ('Rimba', 'Abaji Area Council', 'Abaji axis', 'Abaji Area Council', 'village', ()),
    ('Yaba', 'Abaji Area Council', 'Abaji axis', 'Abaji Area Council', 'village', ()),
    ('Agyana', 'Abaji Area Council', 'Abaji axis', 'Abaji Area Council', 'village', ()),
    ('Abaji town centre', 'Abaji Area Council', 'Abaji axis', 'Abaji', 'neighborhood', ('Abaji town center',)),
)

FCT_LOCATION_SEED.extend(
    _listed_place(name, council, zone, parent, place_type=place_type, aliases=aliases)
    for name, council, zone, parent, place_type, aliases in _ABUJA_DIRECTORY_PLACES
)

FCT_LOCATION_SEED.append(_listed_place(
    'Mpape', 'Bwari Area Council', 'Bwari / north-west',
    'Bwari Area Council', place_type='district', boundary_status='needs_review',
))