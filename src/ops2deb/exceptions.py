class Ops2debError(Exception):
    pass


class GenerateError(Ops2debError):
    pass


class BuildError(GenerateError):
    pass


class GenerateScriptError(GenerateError):
    pass


class FetchError(Ops2debError):
    pass


class UpdaterError(Ops2debError):
    pass


class ParseError(Ops2debError):
    pass


class AptError(Ops2debError):
    pass
