from logging import Formatter, getLogger, INFO, StreamHandler

__all__ = ['logger']


logger = getLogger("network")

logger.info("Initialised logger")
logger.setLevel(INFO)

_handler = StreamHandler()
_handler.setLevel(INFO)
# create formatter and add it to the handlers
formatter = Formatter('%(levelname)s - [%(asctime)s - %(name)s] {%(message)s\}')
_handler.setFormatter(formatter)

logger.addHandler(_handler)