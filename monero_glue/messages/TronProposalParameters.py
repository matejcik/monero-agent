# Automatically generated by pb2py
# fmt: off
from .. import protobuf as p


class TronProposalParameters(p.MessageType):

    def __init__(
        self,
        key: int = None,
        value: int = None,
    ) -> None:
        self.key = key
        self.value = value

    @classmethod
    def get_fields(cls):
        return {
            1: ('key', p.UVarintType, 0),
            2: ('value', p.UVarintType, 0),
        }
