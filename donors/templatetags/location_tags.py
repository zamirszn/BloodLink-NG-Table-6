from django import template
from django.utils.html import json_script

from donors.locations import NIGERIA

register = template.Library()


@register.simple_tag
def location_data():
    """The state -> LGAs map as a JSON <script>, for the dependent dropdowns."""
    return json_script(NIGERIA, "ng-locations")
