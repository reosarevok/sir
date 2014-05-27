# Copyright (c) 2014 Lukas Lalinsky, Wieland Hoffmann
# License: MIT, see LICENSE for details
from collections import defaultdict
from logging import getLogger
from sqlalchemy.orm import class_mapper, Load
from sqlalchemy.orm.interfaces import ONETOMANY, MANYTOONE
from sqlalchemy.orm.properties import RelationshipProperty
from sqlalchemy.orm.query import Query


logger = getLogger("sir")


def merge_paths(field_paths):
    """
    Given a list of paths as ``field_paths``, return a dict that, for each
    level of the path, includes a dictionary whose keys are the columns to load
    and the values are other dictionaries of the described structure.


    :param field_paths:
    :type field_paths: [[str]]
    :rtype: dict
    """
    paths = defaultdict(set)
    for path_list in field_paths:
        for path in path_list:
            path_length = path.count(".")
            current_path_dict = paths
            for i, pathelem in enumerate(path.split(".")):
                # Build a new dict for the next level, if there is one
                if i >= path_length:
                    # If not, still add the current element as a key so its
                    # column gets loaded
                    current_path_dict[pathelem] = ""
                    break

                if pathelem in current_path_dict:
                    new_path_dict = current_path_dict[pathelem]
                else:
                    new_path_dict = defaultdict(set)
                current_path_dict[pathelem] = new_path_dict
                current_path_dict = new_path_dict
    return paths


def defer_everything_but(mapper, load, *columns):
    primary_keys = [c.name for c in mapper.primary_key]
    for prop in mapper.iterate_properties:
        if hasattr(prop, "columns"):
            key = prop.key
            if key not in columns and key[:-3] not in columns and \
               key[-3:] != "_id" and key != "position" and key not in primary_keys:
                # We need the _id columns for subqueries and joins
                # Position is needed because sqla automatically orders by
                # artist_credit_name.position
                logger.debug("Deferring %s on %s", key, mapper)
                load.defer(key)
    return load


class SearchField(object):
    """Represents a searchable field."""
    def __init__(self, name, paths, transformfunc=None):
        """
        :param str name: The name of the field
        :param str path: A dot-delimited path (or a list of them) along which
                         the value of this field can be found, beginning at
                         an instance of the model class this field is bound to.
        :param method transformfunc: An optional function to transform the value
                         before sending it to Solr.
        """
        self.name = name
        if not isinstance(paths, list):
            paths = [paths]
        self.paths = paths
        self.transformfunc = transformfunc


class SearchEntity(object):
    """An an entity with searchable fields."""
    def __init__(self, model, fields, version):
        """
        :param model: A :ref:`declarative <sqla:declarative_toplevel>` class.
        :param list fields: A list of :class:`SearchField` objects.
        :param float version: The supported schema version of this entity.
        """
        self.model = model
        self.fields = fields
        self.query = self.build_entity_query()
        self.version = version

    def build_entity_query(self):
        """
        Builds a :class:`sqla:sqlalchemy.orm.query.Query` object for this
        entity (an instance of :class:`sir.schema.searchentities.SearchEntity`)
        that eagerly loads the values of all search fields.

        :rtype: :class:`sqla:sqlalchemy.orm.query.Query`
        """
        root_model = self.model
        query = Query(root_model)
        paths = [field.paths for field in self.fields]
        merged_paths = merge_paths(paths)

        for field_paths in paths:
            for path in field_paths:
                current_merged_path = merged_paths
                model = root_model
                load = Load(model)
                split_path = path.split(".")
                for pathelem in split_path:
                    current_merged_path = current_merged_path[pathelem]
                    column = getattr(model, pathelem)
                    prop = column.property

                    if isinstance(prop, RelationshipProperty):
                        pk = column.mapper.primary_key[0].name
                        if prop.direction == ONETOMANY:
                            load = load.subqueryload(pathelem)
                        elif prop.direction == MANYTOONE:
                            load = load.joinedload(pathelem)
                        else:
                            load = load.defaultload(pathelem)
                        required_columns = current_merged_path.keys()
                        required_columns.append(pk)
                        # Get the mapper class of the current element of the path so
                        # the next iteration can access it.
                        model = prop.mapper.class_

                        logger.debug("Loading only %s on %s",
                                     required_columns,
                                     model)
                        load = defer_everything_but(class_mapper(model),
                                                    load,
                                                    *required_columns)
                query = query.options(load)
        return query
