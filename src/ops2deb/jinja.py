from jinja2 import Environment, FunctionLoader

from .templates import template_loader

environment = Environment(loader=FunctionLoader(template_loader))
