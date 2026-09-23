from .printables import PrintablesSource
from .makerworld import MakerWorldSource
from .grabcad import GrabCADSource

SOURCES = {s.key: s for s in (PrintablesSource(), MakerWorldSource(), GrabCADSource())}
