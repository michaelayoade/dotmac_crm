"""Network services package.

This package provides services for managing network infrastructure including:
- OLT/PON equipment
- ONT units
- Fiber optic infrastructure (strands, segments, splices, etc.)
- Splitters and FDH cabinets
"""

# Import from OLT services
from app.services.network.olt import (
    OLTDevices,
    PonPorts,
    OntUnits,
    OntAssignments,
    OltShelves,
    OltCards,
    OltCardPorts,
    OltPowerUnits,
    OltSfpModules,
    olt_devices,
    pon_ports,
    ont_units,
    ont_assignments,
    olt_shelves,
    olt_cards,
    olt_card_ports,
    olt_power_units,
    olt_sfp_modules,
)

# Import fiber/splitter services from legacy module
from app.services.network._legacy import (
    # Splitter services
    FdhCabinets,
    Splitters,
    SplitterPorts,
    fdh_cabinets,
    splitters,
    splitter_ports,
    # Fiber services
    FiberStrands,
    FiberSpliceClosures,
    FiberSplices,
    FiberSpliceTrays,
    FiberTerminationPoints,
    FiberSegments,
    fiber_strands,
    fiber_splice_closures,
    fiber_splices,
    fiber_splice_trays,
    fiber_termination_points,
    fiber_segments,
    # PON port splitter links
    PonPortSplitterLinks,
    pon_port_splitter_links,
)

__all__ = [
    # OLT services
    "OLTDevices",
    "olt_devices",
    "PonPorts",
    "pon_ports",
    "OntUnits",
    "ont_units",
    "OntAssignments",
    "ont_assignments",
    "OltShelves",
    "olt_shelves",
    "OltCards",
    "olt_cards",
    "OltCardPorts",
    "olt_card_ports",
    "OltPowerUnits",
    "olt_power_units",
    "OltSfpModules",
    "olt_sfp_modules",
    # Splitter services
    "FdhCabinets",
    "fdh_cabinets",
    "Splitters",
    "splitters",
    "SplitterPorts",
    "splitter_ports",
    # Fiber services
    "FiberStrands",
    "fiber_strands",
    "FiberSpliceClosures",
    "fiber_splice_closures",
    "FiberSplices",
    "fiber_splices",
    "FiberSpliceTrays",
    "fiber_splice_trays",
    "FiberTerminationPoints",
    "fiber_termination_points",
    "FiberSegments",
    "fiber_segments",
    # PON port splitter links
    "PonPortSplitterLinks",
    "pon_port_splitter_links",
]
