from valiance.types import Number, Type

type OverloadSet = dict[tuple[Type, ...], tuple[Type, ...]]
ELEMENT_MAP: dict[str, OverloadSet] = {}


def register_element(symbol: str, inputs: tuple[Type, ...], outputs: tuple[Type, ...]):
    if symbol in ELEMENT_MAP:
        ELEMENT_MAP[symbol][inputs] = outputs
    else:
        ELEMENT_MAP[symbol] = {inputs: outputs}

register_element("+", (Number(), Number()), (Number(),))