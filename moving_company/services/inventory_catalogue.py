from __future__ import annotations


def _items(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split('|') if item.strip())


RESIDENTIAL_APARTMENT = {
    'Bedroom': _items('Single bed|Double bed|Queen bed|King bed|Bed frame|Mattress|Wardrobe|Sliding wardrobe|Chest of drawers|Bedside table|Dressing table|Dressing mirror|Shoe rack|Clothes boxes|Laundry basket|Baby cot|Bunk bed|Baby changing table'),
    'Living Room': _items('2-seater sofa|3-seater sofa|L-shaped sofa|Armchair|Recliner|Coffee table|TV stand|Console table|Side table|Bookshelf|Display cabinet|Ottoman|Rug|Floor lamp|Standing lamp|Large mirror|Wall art|Curtains/blinds'),
    'Dining': _items('4-seater dining table|6-seater dining table|8-seater dining table|Dining chairs|Bar stools|Dining cabinet|Crockery cabinet'),
    'Kitchen': _items('Refrigerator|Double-door refrigerator|Freezer|Gas cooker|Electric cooker|Microwave|Dishwasher|Washing machine|Kitchen cabinet|Kitchen island|Kitchen cart|Blender|Air fryer|Food processor|Crockery|Pots and pans|Kitchen boxes|Water dispenser'),
    'Electronics': _items('Small TV|Medium TV|Large TV|Sound system|Speakers|Decoder|Game console|Desktop computer|Monitor|Laptop|Printer|Router|UPS|Projector|Standing fan|Table fan|Air conditioner|Home theatre'),
    'Bathroom': _items('Bathroom cabinet|Vanity unit|Bathroom mirror|Large mirror|Shower door|Toilet|Bidet|Bathtub|Water heater|Bathroom shelf|Storage rack'),
    'Children': _items("Baby cot|Baby mattress|Stroller|High chair|Baby walker|Children's bed|Bunk bed|Children's desk|Children's chair|Toy box|Toys|Dollhouse|Children's bicycle|Play kitchen"),
    'Fragile / Valuable': _items('Artwork|Paintings|Glass tabletop|Glass cabinet|Mirrors|Chandelier|Sculpture|Piano|Antique furniture|Musical instruments|Safe'),
}

RESIDENTIAL_ADDITIONAL = {
    'Garage / Storage': _items('Tool box|Tool cabinet|Workbench|Ladder|Bicycle|Motorcycle|Generator|Lawn mower|Pressure washer|Storage boxes|Suitcases|Car tyres|Car parts'),
    'Fitness': _items('Treadmill|Exercise bike|Spin bike|Elliptical|Rowing machine|Weight bench|Dumbbell set|Kettlebells|Weight plates|Barbell|Punching bag|Home gym machine|Exercise mats'),
    'Outdoor / Balcony': _items('Outdoor sofa|Outdoor chairs|Outdoor table|Garden bench|Swing chair|Garden umbrella|BBQ grill|Plant pots|Plants|Garden tools|Hose reel|Outdoor storage box'),
}

OFFICE = {
    'Office Furniture': _items('Office desk|Large office desk|Executive desk|Reception desk|Office chair|Executive chair|Visitor chair|Conference table|Meeting table|Filing cabinet|Storage cabinet|Bookshelf|Office shelves|Reception sofa|Waiting-area chairs|Whiteboard|Notice board'),
    'IT & Electronics': _items('Desktop computer|Monitor|Laptop|Printer|Large printer|Photocopier|Scanner|Projector|Projector screen|Server|Server rack|Network switch|Router|UPS|CCTV equipment|Telephone|Shredder'),
    'Office Equipment': _items('Water dispenser|Refrigerator|Microwave|Safe|Document boxes|Archive boxes|Files|Office supplies|Paper boxes'),
    'Special Handling': _items('Heavy safe|Server rack|Large photocopier|IT equipment|Fragile electronics'),
}

SHOP = {
    'Shop Fixtures': _items('Display shelf|Display cabinet|Glass display cabinet|Wall shelving|Gondola shelf|Counter|Checkout counter|Cashier desk|Storage cabinet|Rack|Clothing rack|Mannequin|Display table|Shopping baskets|Shopping carts'),
    'Electronics': _items('POS terminal|POS computer|Monitor|Printer|Barcode scanner|Receipt printer|CCTV equipment|TV|Sound system|Refrigerator|Freezer'),
    'Stock / Storage': _items('Small boxes|Medium boxes|Large boxes|Storage bins|Plastic containers|Pallets|Bags/sacks|Merchandise'),
    'Shop Equipment': _items('Generator|Safe|Fan|Air conditioner|Water dispenser|Microwave'),
}

WAREHOUSE = {
    'Storage': _items('Pallets|Storage racks|Industrial shelving|Metal shelves|Storage bins|Plastic crates|Large containers|Boxes|Cartons|Drums|Stock / Cartons'),
    'Equipment': _items('Generator|Industrial machine|Compressor|Welding machine|Workbench|Tool cabinet|Pallet jack|Hand truck|Ladder|Trolley|Forklift'),
    'Heavy Items': _items('Industrial equipment|Machinery|Motors|Pumps|Compressors|Large generators|Construction equipment'),
}

HOTEL = {
    'Guest Room': _items('Single bed|Double bed|Queen bed|King bed|Mattress|Bedside table|Wardrobe|Dressing table|Dressing mirror|TV|TV stand|Refrigerator|Safe|Chair|Desk|Curtains|Lamps'),
    'Restaurant / Dining': _items('Dining table|Dining chair|Bar stool|Bar table|Serving trolley|Display cabinet'),
    'Kitchen': _items('Commercial refrigerator|Commercial freezer|Oven|Cooker|Dishwasher|Microwave|Kitchen equipment|Pots|Pans|Crockery|Cutlery|Food preparation equipment'),
    'Reception / Lobby': _items('Reception desk|Sofa|Armchairs|Coffee table|Display cabinet|Computer|Printer|POS equipment'),
    'Hotel Operations': _items('Laundry machine|Dryer|Ironing equipment|Linen carts|Storage shelves|Cleaning equipment|Generator'),
}

ESTATE = {
    'Estate Office': _items('Office desk|Office chair|Filing cabinet|Computer|Printer|CCTV equipment|Safe'),
    'Security': _items('Security desk|Security chairs|CCTV equipment|Monitor|Gate equipment|Barrier equipment|Communication equipment'),
    'Maintenance': _items('Generator|Lawn mower|Pressure washer|Tool box|Tool cabinet|Ladder|Workbench|Garden tools|Maintenance equipment'),
    'Common Areas': _items('Outdoor chairs|Outdoor tables|Benches|Playground equipment|Fitness equipment|Pool equipment|Waste bins|Storage equipment'),
}

CONSTRUCTION_SITE = {
    'Construction Equipment': _items('Generator|Cement mixer|Concrete mixer|Compressor|Welding machine|Cutting machine|Drilling machine|Power tools|Industrial tools|Pump|Water pump'),
    'Heavy Equipment': _items('Construction machinery|Industrial machinery|Heavy motors|Large generators|Metal equipment|Steel structures'),
    'Tools': _items('Tool boxes|Tool cabinets|Wheelbarrows|Ladders|Scaffolding|Trolleys|Hand trucks'),
    'Materials': _items('Cement bags|Tiles|Blocks|Pipes|Timber|Steel rods|Metal sheets|Doors|Windows|Glass|Electrical materials|Plumbing materials'),
}

PROPERTY_CATALOGUE = {
    'Apartment': RESIDENTIAL_APARTMENT,
    'House': {**RESIDENTIAL_APARTMENT, **RESIDENTIAL_ADDITIONAL},
    'Duplex': {**RESIDENTIAL_APARTMENT, **RESIDENTIAL_ADDITIONAL},
    'Office': OFFICE,
    'Shop': SHOP,
    'Warehouse': WAREHOUSE,
    'Hotel': HOTEL,
    'Estate': ESTATE,
    'Construction Site': CONSTRUCTION_SITE,
}

PROPERTY_CATALOGUE['Estate'] = {
    **ESTATE,
    'Residential Unit Inventory': _items('Apartment|House|Duplex'),
}

MATERIAL_UNITS = {
    'Cement bags': 'bags', 'Tiles': 'cartons', 'Blocks': 'pieces', 'Pipes': 'pieces',
    'Timber': 'pieces', 'Steel rods': 'pieces', 'Metal sheets': 'sheets',
    'Doors': 'pieces', 'Windows': 'pieces', 'Glass': 'panes',
    'Electrical materials': 'boxes', 'Plumbing materials': 'boxes',
}

BULK_ITEM_OPTIONS = {
    'Warehouse': {'Stock / Cartons': 'cartons'},
    'Construction Site': MATERIAL_UNITS,
}

ALL_CATALOGUE_ITEMS = tuple(dict.fromkeys(
    name
    for property_groups in PROPERTY_CATALOGUE.values()
    for names in property_groups.values()
    for name in names
    if name not in {'Apartment', 'House', 'Duplex'}
))

CATEGORY_BY_ITEM = {}
for _property_groups in PROPERTY_CATALOGUE.values():
    for _category, _names in _property_groups.items():
        for _name in _names:
            if _name not in {'Apartment', 'House', 'Duplex'}:
                CATEGORY_BY_ITEM.setdefault(_name, _category)

INVENTORY_CATALOG = {
    category: tuple(name for name in ALL_CATALOGUE_ITEMS if CATEGORY_BY_ITEM[name] == category)
    for category in dict.fromkeys(CATEGORY_BY_ITEM.values())
}
