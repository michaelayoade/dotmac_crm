"""Network services package.

This package provides services for managing network infrastructure including:
- OLT/PON equipment
- ONT units
- Fiber optic infrastructure (strands, segments, splices, etc.)
- Splitters and FDH cabinets
"""

# Import from OLT services
# Import fiber/splitter services from legacy module
from app.services.network._legacy import (
    # Splitter services
    FdhCabinets,
    FiberSegments,
    FiberSpliceClosures,
    FiberSplices,
    FiberSpliceTrays,
    # Fiber services
    FiberStrands,
    FiberTerminationPoints,
    # PON port splitter links
    PonPortSplitterLinks,
    SplitterPorts,
    Splitters,
    fdh_cabinets,
    fiber_segments,
    fiber_splice_closures,
    fiber_splice_trays,
    fiber_splices,
    fiber_strands,
    fiber_termination_points,
    pon_port_splitter_links,
    splitter_ports,
    splitters,
)
from app.services.network.olt import (
    OltCardPorts,
    OltCards,
    OLTDevices,
    OltPowerUnits,
    OltSfpModules,
    OltShelves,
    OntAssignments,
    OntUnits,
    PonPorts,
    olt_card_ports,
    olt_cards,
    olt_devices,
    olt_power_units,
    olt_sfp_modules,
    olt_shelves,
    ont_assignments,
    ont_units,
    pon_ports,
)

__all__ = [
    # Splitter services
    "FdhCabinets",
    "FiberSegments",
    "FiberSpliceClosures",
    "FiberSpliceTrays",
    "FiberSplices",
    # Fiber services
    "FiberStrands",
    "FiberTerminationPoints",
    # OLT services
    "OLTDevices",
    "OltCardPorts",
    "OltCards",
    "OltPowerUnits",
    "OltSfpModules",
    "OltShelves",
    "OntAssignments",
    "OntUnits",
    # PON port splitter links
    "PonPortSplitterLinks",
    "PonPorts",
    "SplitterPorts",
    "Splitters",
    "fdh_cabinets",
    "fiber_segments",
    "fiber_splice_closures",
    "fiber_splice_trays",
    "fiber_splices",
    "fiber_strands",
    "fiber_termination_points",
    "olt_card_ports",
    "olt_cards",
    "olt_devices",
    "olt_power_units",
    "olt_sfp_modules",
    "olt_shelves",
    "ont_assignments",
    "ont_units",
    "pon_port_splitter_links",
    "pon_ports",
    "splitter_ports",
    "splitters",
]
