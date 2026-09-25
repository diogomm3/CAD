from .printables import PrintablesSource
from .makerworld import MakerWorldSource

SOURCES = {s.key: s for s in (PrintablesSource(), MakerWorldSource())}
