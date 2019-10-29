from typing import Union, Any, List, Tuple

tCluedStr = Tuple[str, Any]
tTest = Union[str, tCluedStr]
tFloatStr = Tuple[float, str]
tAnswer = Union[float, tFloatStr]

SUITE_SIZE = 10


def generate() -> List[Union[tTest, List[tTest]]]:
    return ["" for _ in range(SUITE_SIZE)]


def solve(data: str) -> str:
    return "42"


def check(output, solved_or_clue: Union[str, Any]) -> tAnswer:
    return output == "42"


def evaluate(rates: List[tAnswer]) -> tAnswer:
    """ Агрегирование оценок за все тесты

    :param values: Оценки за все выполненные тесты
    :return:
    """
    return 0.88 if all(rates) else 0
