from ..models import ServicePricing


def get_pricing_map():
    return {row.key: float(row.value) for row in ServicePricing.query.all()}