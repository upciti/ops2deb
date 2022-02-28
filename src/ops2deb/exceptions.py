class Ops2debError(Exception):
    pass


class Ops2debGeneratorError(Ops2debError):
    pass


class Ops2debGeneratorScriptError(Ops2debGeneratorError):
    pass


class Ops2debBuilderError(Ops2debGeneratorError):
    pass


class Ops2debFetcherError(Ops2debError):
    pass


class Ops2debExtractError(Ops2debFetcherError):
    pass


class Ops2debUpdaterWarning(Ops2debError):
    pass


class Ops2debUpdaterError(Ops2debError):
    pass


class Ops2debParserError(Ops2debError):
    pass


class Ops2debAptError(Ops2debError):
    pass


class Ops2debFormatterError(Ops2debError):
    pass
