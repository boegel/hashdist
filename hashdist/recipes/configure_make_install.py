from textwrap import dedent

from ..core import BuildSpec

from .recipes import Recipe, FetchSourceCode

class ConfigureMakeInstall(Recipe):
    def __init__(self, name, version, source_url, source_key,
                 configure_flags=[], strip=None, **kw):
        if strip is None:
            strip = 0 if source_key.startswith('git:') else 1
        source_fetches = [FetchSourceCode(source_url, source_key, strip=strip)]
        Recipe.__init__(self, name, version, source_fetches, **kw)
        self.configure_flags = configure_flags

    def get_commands(self):
        return [
            ['which', 'gcc'],
            ['LDFLAGS=$HDIST_ABS_LDFLAGS', './configure', '--prefix=${TARGET}'] +
            self.configure_flags,
            ['make'],
            ['make', 'install']
            ]

