import sys

from .. import core
from ..hdist_logging import colorize

from pprint import pprint

class BaseSourceFetch(object):
    def __init__(self, key, target, strip=0):
        self.key = key
        self.target = target
        self.strip = strip
        
    def get_spec(self):
        return {'key': self.key,
                'target': self.target,
                'strip': self.strip}

class FetchSourceCode(BaseSourceFetch):
    def __init__(self, url, key, target='.', strip=0):
        BaseSourceFetch.__init__(self, key, target, strip)
        self.url = url

    def fetch_into(self, source_cache):
        source_cache.fetch(self.url, self.key)
                        
class PutScript(BaseSourceFetch):
    def __init__(self, files, target='.'):
        key = hdist_pack(files)
        BaseSourceFetch.__init__(self, key, target)
        self.files = files

    def fetch_into(self, source_cache):
        source_cache.put(self.files)


class Recipe(object):

    """

    Each recipe can be constructable/pickleable without any actions taken
    or the build spec assembled; i.e., a recipe object should be lazy.
    
    Parameters
    ----------

    constrain_to_after : list of Recipe
        Any recipe using this recipe must list this recipe first in PATH.
        E.g., this describes the relationship between ccache and gcc.
    
    """

    
    def __init__(self, name, version, source_fetches=(), dependencies=None,
                 env=None, is_virtual=False, constrain_to_after=(), **kw):
        self.name = name
        self.version = version
        self.source_fetches = source_fetches
        self.is_virtual = is_virtual
        self.constrain_to_after = constrain_to_after

        dependencies = dict(dependencies) if dependencies is not None else {}
        env = dict(env) if env is not None else {}

        # parse kw to mean dependency if Recipe, or env entry otherwise
        for key, value in kw.iteritems():
            if isinstance(value, Recipe):
                dependencies[key] = value
            elif isinstance(value, (str, int, float)):
                env[key] = value
            else:
                raise TypeError('Meaning of passing argument %s of type %r not understood' %
                                (key, type(value)))


        self.dependencies = dependencies
        self.env = env
        self._build_spec = None

    def get_build_spec(self):
        """
        Returns
        -------

        build_spec : BuildSpec
        
        """
        # the build spec is cached; this is important when fetching dependency
        # artifact IDs
        if self._build_spec is not None:
            return self._build_spec
        else:
            return self._assemble_build_spec()

    def get_artifact_id(self):
        if self.is_virtual:
            return 'virtual:%s' % self.name
        else:
            return self.get_real_artifact_id()

    def get_real_artifact_id(self):
        return self.get_build_spec().artifact_id

    def get_display_name(self):
        if self.is_virtual:
            return self.get_artifact_id()
        else:
            return core.short_artifact_id(artifact_id, 4) + '..'

    def fetch_sources(self, source_cache):
        for fetch in self.source_fetches:
            fetch.fetch_into(source_cache)

    def format_tree(self, build_store=None, use_colors=True):
        lines = []
        self._format_tree(lines, {}, 0, build_store, use_colors)
        return '\n'.join(lines)

    def _format_tree(self, lines, visited, level, build_store, use_colors):
        indent_str = '  '
        indent = indent_str * level
        build_spec = self.get_build_spec()
        artifact_id = build_spec.artifact_id
        short_artifact_id = core.shorten_artifact_id(artifact_id, 6) + '..'

        def add_line(left, right):
            lines.append('%-70s%s' % (left, right))

        if build_store is None:
            status = ''
        else:
            status = (colorize(' [ok]', 'bold-blue', use_colors)
                      if build_store.is_present(build_spec)
                      else colorize(' [needs build]', 'bold-red', use_colors))
        
        if artifact_id in visited:
            display_name = visited[artifact_id]
            desc = '%s%s (see above)' % (indent, display_name)
        elif self.is_virtual:
            display_name = self.get_artifact_id()
            desc = '%s%s (=%s)' % (indent, display_name, short_artifact_id)
        else:
            display_name = short_artifact_id
            desc = '%s%s' % (indent, display_name)
        add_line(desc, status)

        visited[artifact_id] = display_name
        
        # bin all repeated artifacts on their own line
        repeated = []
        for dep_name, dep in sorted(self.dependencies.items()):
            if dep.get_real_artifact_id() in visited:
                repeated.append(dep.get_display_name())
            else:
                dep._format_tree(lines, visited, level + 1, build_store, use_colors)
        if repeated:
            add_line(indent + indent_str + ','.join(repeated),
                     colorize(' (see above)', 'yellow', use_colors))

    def __repr__(self):
        return '<Recipe for %s>' % self.get_artifact_id()

    def _assemble_build_spec(self):
        sources = []
        for fetch in self.source_fetches:
            sources.append(fetch.get_spec())

        dep_specs = self.get_dependencies_spec()

        commands = self.get_commands()
        files = self.get_files()
        parameters = self.get_parameters()
        env = self.get_env()
        
        doc = dict(name=self.name, version=self.version, sources=sources, env=env,
                   commands=commands, files=files, dependencies=dep_specs,
                   parameters=parameters)
        return core.BuildSpec(doc)

    # Subclasses may override the following

    def get_dependencies_spec(self):
        problem = [(dep_name, dep.get_artifact_id(),
                    [x.get_artifact_id() for x in dep.constrain_to_after])
                   for dep_name, dep in self.dependencies.iteritems()]
        sorted_names = order_by_constraints(problem)

        dep_specs = []
        for dep_name in sorted_dependencies:
            dep = self.dependencies[dep_name]
            dep_id = dep.get_artifact_id()
            dep_specs.append({"ref": dep_name, "id": dep_id, "in_path": True,
                              "in_hdist_compiler_paths": True})
        return dep_specs
    
    def get_commands(self):
        return []

    def get_files(self):
        return []

    def get_parameters(self):
        return {}

    def get_env(self):
        return {}

def find_dependency_in_spec(spec, ref):
    """Utility to return the dict corresponding to the given ref
    in a dependency build spec document fragment
    """
    for item in spec:
        if item['ref'] == ref:
            return item

def order_by_constraints(problem):
    """Order items by a set of constraints, stabilized by a key

    `problem` is a list of tuples ``(key, obj, [after_obj, ...])``.
    The result is all the `obj` items in an order such that, in each case,
    `obj` comes after `after_obj`. `key` is used to determine the order
    after the constraints are satisfied (the result may not be perfectly
    sorted, but it will be stable).

    `obj` must be hashable, `key` must be comparable

    The concrete algorithm is to first invert the DAG (each object knows
    which ones it should come before), then start at the roots of this
    DAG and form sub-trees using DFS (visiting children in order sorted
    by their key); then finally output the sub-trees ordered by their
    key.
    
    """
    # more convenient with dict for keys
    keys = dict((tup[1], tup[0]) for tup in problem)
    # turn into dict-based graph with reverse-sorted edge lists, and identify roots
    graph = {}
    roots = set(tup[1] for tup in problem)
    for key, obj, after_lst in problem:
        after_lst = sorted(after_lst, key=keys.__getitem__, reverse=True)
        graph[obj] = after_lst
        roots.difference_update(after_lst)

    result = []

    def dfs(node):
        if node not in result:
            for child in graph[node]:
                dfs(child)
            result.append(node)

    for obj in sorted(roots, key=keys.__getitem__):
        dfs(obj)

    return result


class HdistTool(Recipe):
    def __init__(self):
        Recipe.__init__(self, core.HDIST_CLI_ARTIFACT_NAME, core.HDIST_CLI_ARTIFACT_VERSION,
                        is_virtual=True)

    def _assemble_build_spec(self):
        return core.hdist_cli_build_spec()

hdist_tool = HdistTool()


def build_recipes(build_store, source_cache, recipes, **kw):
    built = set() # artifact_id
    virtuals = {} # virtual_name -> artifact_id

    def _depth_first_build(recipe):
        for dep_name, dep_pkg in recipe.dependencies:
            # recurse
            _depth_first_build(dep_pkg)

        recipe.fetch_sources(source_cache)

        build_spec = recipe.get_build_spec()
        if not build_spec.artifact_id in built:
            # todo: move to in-memory cache in BuildStore
            build_store.ensure_present(build_spec, source_cache, virtuals=virtuals,
                                       **kw)
            built.add(build_spec.artifact_id)

        if recipe.is_virtual:
            virtuals[recipe.get_artifact_id()] = build_spec.artifact_id

    
    for recipe in recipes:
        _depth_first_build(recipe)    
    
