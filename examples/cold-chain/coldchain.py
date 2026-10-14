"""What the cold-chain demo's controllers, management platform and gate agree on.

The estate is 12 stores with 3 refrigerated cabinets each. Every number here
is a demo setting: the bands and the breach limits are plausible for frozen
and chilled display cabinets, and nothing in this demo models any vendor's
equipment.
"""

STORES = [
    "store-0017", "store-0042", "store-0108", "store-0233", "store-0417", "store-0512",
    "store-0621", "store-0730", "store-0815", "store-0904", "store-1011", "store-1120",
]
CABINETS = {"frozen-01": "frozen", "frozen-02": "frozen", "chilled-01": "chilled"}

# The temperatures each kind of cabinet is approved to run at. This table is
# controller-side configuration: the management platform can't change it.
BANDS = {"frozen": (-24.0, -15.0), "chilled": (0.0, 5.0)}

# Where each cabinet starts, and above what temperature the demo counts its
# cold chain as broken.
START = {"frozen": -20.0, "chilled": 3.0}
BREACH = {"frozen": -12.0, "chilled": 8.0}

COMMANDS = ("set_temperature", "start_defrost", "stop_compressor", "change_alarm_threshold")


def cabinet_ids():
    return [f"{store}/{cabinet}" for store in STORES for cabinet in CABINETS]


def kind(cabinet_id):
    return CABINETS[cabinet_id.split("/", 1)[1]]


def action_for(cabinet_id, command, value=None):
    """The gated action a command amounts to.

    The controller calls this on the command it is about to execute, so the
    action it asks the gate to confirm describes what will physically happen,
    whatever the sender claimed. Returns None for a command it doesn't know.
    """
    if command == "set_temperature":
        low, high = BANDS[kind(cabinet_id)]
        if low <= value <= high:
            return "refrigeration.setpoint.in_band"
        return "refrigeration.setpoint.out_of_band"
    return {
        "start_defrost": "refrigeration.defrost.start",
        "stop_compressor": "refrigeration.compressor.stop",
        "change_alarm_threshold": "refrigeration.alarm.change",
    }.get(command)
