# Copyright 2017 Planet Labs, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import re

import click
from click.types import CompositeParamType

from .util import read

from planet.api import filters
from planet.api.utils import geometry_from_json
from planet.api.utils import strp_lenient

_allowed_item_types = [
    "PSScene4Band", "PSScene3Band", "REScene",
    "REOrthoTile", "Sentinel2L1C", "PSOrthoTile", "Landsat8L1G"]

_allowed_asset_types = [
    "analytic", "analytic_b1", "analytic_b10", "analytic_b11", "analytic_b12",
    "analytic_b2", "analytic_b3", "analytic_b4", "analytic_b5", "analytic_b6",
    "analytic_b7", "analytic_b8", "analytic_b8a", "analytic_b9",
    "analytic_bqa", "analytic_dn", "analytic_dn_xml", "analytic_ms",
    "analytic_xml", "basic_analytic", "basic_analytic_b1",
    "basic_analytic_b1_nitf", "basic_analytic_b2", "basic_analytic_b2_nitf",
    "basic_analytic_b3", "basic_analytic_b3_nitf", "basic_analytic_b4",
    "basic_analytic_b4_nitf", "basic_analytic_b5", "basic_analytic_b5_nitf",
    "basic_analytic_dn", "basic_analytic_dn_nitf", "basic_analytic_dn_rpc",
    "basic_analytic_dn_rpc_nitf", "basic_analytic_dn_xml",
    "basic_analytic_dn_xml_nitf", "basic_analytic_nitf", "basic_analytic_rpc",
    "basic_analytic_rpc_nitf", "basic_analytic_sci", "basic_analytic_xml",
    "basic_analytic_xml_nitf", "basic_udm", "browse", "metadata_aux",
    "metadata_txt", "udm", "visual", "visual_xml"
]

metavar_docs = {
    'FIELD COMP VALUE...': '''A comparison query format where FIELD is a
    property of the item-type and COMP is one of lt, lte, gt, gte and VALUE is
    the number or date to compare against.

    ISO-8601 variants are supported. For example, `2017` is short for the full
    `2017-01-01T00:00:00+00:00`.
    ''',
    'FIELD VALUES...': '''Specifies an 'in' query where FIELD is a property
    of the item-type and VALUES is space or comma separated text or numbers.
    ''',
    'GEOM': '''Specify a geometry in GeoJSON format either as an inline value,
    stdin, or a file. `@-` specifies stdin and `@filename` specifies reading
    from a file named 'filename'. Other wise, the value is assumed to be
    GeoJSON.
    ''',
    'FILTER': '''Specify a Data API search filter provided as JSON.
    `@-` specifies stdin and `@filename` specifies reading from a file named
    'filename'. Other wise, the value is assumed to be JSON.
    ''',
    'ITEM-TYPE': '''Specify Item-Type(s) of interest. Case-insensitive,
    supports glob-matching, e.g. `psscene*` means `PSScene3Band` and
    `PSScene4Band`. The `all` value specifies every Item-Type.
    ''',
    'ASSET-TYPE': '''Specify Asset-Type(s) of interest. Case-insenstive,
    supports glob-matching, e.g. `visual*` specifies `visual` and `visual_xml`.
    '''
}


class _LenientChoice(click.Choice):
    '''Like click.Choice but allows
    case-insensitive prefix matching
    optional 'all' matching
    optional prefix matching
    glob matching
    format fail msges for large selection of choices

    returns a list unlike choice (to support 'all')
    '''

    allow_all = False
    allow_prefix = False

    def get_metavar(self, param):
        return self.name.upper()

    def _fail(self, msg, val, param, ctx):
        self.fail('%s choice: %s.\nChoose from:\n\t%s' %
                  (msg, val, '\n\t'.join(self.choices)), param, ctx)

    def convert(self, val, param, ctx):
        lval = val.lower()
        if lval == 'all' and self.allow_all:
            return self.choices
        if '*' in lval:
            pat = lval.replace('*', '.*')
            matches = [c for c in self.choices
                       if re.match(pat, c.lower())]
        elif self.allow_prefix:
            matches = [c for c in self.choices
                       if c.lower().startswith(lval)]
        else:
            matches = [c for c in self.choices if c.lower() == lval]
        if not matches:
            self._fail('invalid', val, param, ctx)
        else:
            return matches


class ItemType(_LenientChoice):
    name = 'item-type'
    allow_all = True
    allow_prefix = True

    def __init__(self):
        _LenientChoice.__init__(self, _allowed_item_types)


class AssetType(_LenientChoice):
    name = 'asset-type'

    def __init__(self):
        _LenientChoice.__init__(self, _allowed_asset_types)


class _FilterFieldValues(CompositeParamType):
    name = 'field values'
    arity = 2

    def convert(self, val, param, ctx):
        field, vals = val
        vals = re.split('\s+|,', vals)
        parsed = []
        for v in vals:
            v = v.strip()
            if not v:
                continue
            try:
                parsed.append(self.val_type(v))
            except ValueError:
                self.fail('invalid value: %s' % v, param, ctx)
        return self._builder(field, *parsed)


class StringIn(_FilterFieldValues):
    val_type = str

    @property
    def _builder(self):
        return filters.string_filter


class NumberIn(_FilterFieldValues):
    val_type = float

    @property
    def _builder(self):
        return filters.num_filter


class Range(CompositeParamType):
    arity = 3
    name = 'field comp value'
    comp_ops = ['lt', 'lte', 'gt', 'gte']

    @property
    def _builder(self):
        return filters.range_filter

    def _parse(self, val, param, ctx):
        return val

    def convert(self, vals, param, ctx):
        field, comp_op, val = vals
        if comp_op not in self.comp_ops:
            self.fail('invalid operator: %s. allowed: %s' % (
                comp_op, ','.join(self.comp_ops)), param, ctx)
        args = dict([(comp_op, self._parse(val, param, ctx))])
        return self._builder(field, **args)


class DateRange(Range):
    @property
    def _builder(self):
        return filters.date_range

    def _parse(self, val, param, ctx):
        parsed = strp_lenient(val)
        if parsed is None:
            self.fail('invalid date: %s.' % val, param, ctx)
        return parsed


class GeomFilter(click.ParamType):
    name = 'geom'

    def convert(self, val, param, ctx):
        val = read(val)
        if not val:
            return []
        try:
            geoj = json.loads(val)
        except ValueError:
            raise click.BadParameter('invalid GeoJSON')
        geom = geometry_from_json(geoj)
        if geom is None:
            raise click.BadParameter('unable to find geometry in input')
        return [filters.geom_filter(geom)]


class FilterJSON(click.ParamType):
    name = 'filter'

    def convert(self, val, param, ctx):
        val = read(val)
        if not val:
            return []
        try:
            filt = json.loads(val)
        except ValueError:
            raise click.BadParameter('invalid JSON')
        return filt
