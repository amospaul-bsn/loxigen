# Copyright 2013, Big Switch Networks, Inc.
#
# LoxiGen is licensed under the Eclipse Public License, version 1.0 (EPL), with
# the following special exception:
#
# LOXI Exception
#
# As a special exception to the terms of the EPL, you may distribute libraries
# generated by LoxiGen (LoxiGen Libraries) under the terms of your choice, provided
# that copyright and licensing notices generated by LoxiGen are not altered or removed
# from the LoxiGen Libraries and the notice provided below is (i) included in
# the LoxiGen Libraries, if distributed in source code form and (ii) included in any
# documentation for the LoxiGen Libraries, if distributed in binary form.
#
# Notice: "Copyright 2013, Big Switch Networks, Inc. This library was generated by the LoxiGen Compiler."
#
# You may not use this file except in compliance with the EPL or LOXI Exception. You may obtain
# a copy of the EPL at:
#
# http://www.eclipse.org/legal/epl-v10.html
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# EPL for the specific language governing permissions and limitations
# under the EPL.

# Prototype of an Intermediate Object model for the java code generator
# A lot of this stuff could/should probably be merged with the python utilities

import collections
from collections import namedtuple, defaultdict, OrderedDict
import logging
import os
import pdb
import re

from generic_utils import find, memoize, OrderedSet, OrderedDefaultDict
import of_g
from loxi_ir import *
import loxi_front_end.type_maps as type_maps
import loxi_utils.loxi_utils as loxi_utils
import py_gen.util as py_utils
import test_data

import java_gen.java_type as java_type

class JavaModel(object):
    enum_blacklist = set(("OFDefinitions",))
    enum_entry_blacklist = defaultdict(lambda: set(), OFFlowWildcards=set([ "NW_DST_BITS", "NW_SRC_BITS", "NW_SRC_SHIFT", "NW_DST_SHIFT" ]))
    write_blacklist = defaultdict(lambda: set(), OFOxm=set(('typeLen',)), OFAction=set(('type',)), OFInstruction=set(('type',)))
    virtual_interfaces = set(['OFOxm', 'OFAction', 'OFInstruction' ])

    @property
    @memoize
    def versions(self):
        return OrderedSet( JavaOFVersion(raw_version) for raw_version in of_g.target_version_list )

    @property
    @memoize
    def interfaces(self):
        version_map_per_class = collections.OrderedDict()

        for raw_version, of_protocol in of_g.ir.items():
            jversion = JavaOFVersion(of_protocol.wire_version)

            for of_class in of_protocol.classes:
                if not of_class.name in version_map_per_class:
                    version_map_per_class[of_class.name] = collections.OrderedDict()

                version_map_per_class[of_class.name][jversion] = of_class

        interfaces = []
        for class_name, version_map in version_map_per_class.items():
            interfaces.append(JavaOFInterface(class_name, version_map))

        return interfaces

    @property
    @memoize
    def enums(self):
        name_version_enum_map = OrderedDefaultDict(lambda: OrderedDict())

        for version in self.versions:
            of_protocol = of_g.ir[version.int_version]
            for enum in of_protocol.enums:
                name_version_enum_map[enum.name][version] = enum

        enums = [ JavaEnum(name, version_enum_map) for name, version_enum_map,
                        in name_version_enum_map.items() ]

        # inelegant - need java name here
        enums = [ enum for enum in enums if enum.name not in self.enum_blacklist ]
        return enums

    @memoize
    def enum_by_name(self, name):
        try:
            return find(self.enums, lambda e: e.name == name)
        except KeyError:
            raise KeyError("Could not find enum with name %s" % name)

    @property
    @memoize
    def of_factory(self):
           return OFFactory(
                    package="org.openflow.protocol",
                    name="OFFactory",
                    members=self.interfaces)

    def generate_class(self, clazz):
        if clazz.interface.is_virtual:
            return False
        if clazz.interface.name == "OFTableMod":
            return False
        if clazz.interface.name.startswith("OFMatchV"):
            return True
        if loxi_utils.class_is_message(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_oxm(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_action(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_instruction(clazz.interface.c_name):
            return True
        else:
            return False


class OFFactory(namedtuple("OFFactory", ("package", "name", "members"))):
    @property
    def factory_classes(self):
            return [ OFFactoryClass(
                    package="org.openflow.protocol.ver{}".format(version.of_version),
                    name="OFFactoryVer{}".format(version.of_version),
                    interface=self,
                    version=version
                    ) for version in model.versions ]


OFGenericClass = namedtuple("OFGenericClass", ("package", "name"))
OFFactoryClass = namedtuple("OFFactory", ("package", "name", "interface", "version"))

model = JavaModel()

#######################################################################
### OFVersion
#######################################################################

class JavaOFVersion(object):
    """ Models a version of OpenFlow. contains methods to convert the internal
        Loxi version to a java constant / a string """
    def __init__(self, int_version):
        self.int_version = int(int_version)

    @property
    def of_version(self):
        return "1" + str(int(self.int_version) - 1)

    @property
    def constant_version(self):
        return "OF_" + self.of_version

    def __repr__(self):
        return "JavaOFVersion(%d)" % self.int_version

    def __str__(self):
        return of_g.param_version_names[self.int_version]

    def __hash__(self):
        return hash(self.int_version)

    def __eq__(self, other):
        if other is None or type(self) != type(other):
            return False
        return (self.int_version,) == (other.int_version,)

#######################################################################
### Interface
#######################################################################

class JavaOFInterface(object):
    """ Models an OpenFlow Message class for the purpose of the java class.
        Version agnostic, in contrast to the loxi_ir python model.
    """
    def __init__(self, c_name, version_map):
        self.c_name = c_name
        self.version_map = version_map
        self.name = java_type.name_c_to_caps_camel(c_name)
        self.variable_name = self.name[2].lower() + self.name[3:]
        self.constant_name = c_name.upper().replace("OF_", "")

        pck_suffix, parent_interface = self.class_info()
        self.package = "org.openflow.protocol.%s" % pck_suffix if pck_suffix else "org.openflow.protocol"
        if self.name != parent_interface:
            self.parent_interface = parent_interface
        else:
            self.parent_interface = None

    def class_info(self):
        if re.match(r'OFFlow(Add|Modify(Strict)?|Delete(Strict)?)$', self.name):
            return ("", "OFFlowMod")
        elif re.match(r'OFMatch.*', self.name):
            return ("", "Match")
        elif loxi_utils.class_is_message(self.c_name):
            return ("", "OFMessage")
        elif loxi_utils.class_is_action(self.c_name):
            return ("action", "OFAction")
        elif loxi_utils.class_is_oxm(self.c_name):
            return ("oxm", "OFOxm")
        elif loxi_utils.class_is_instruction(self.c_name):
            return ("instruction", "OFInstruction")
        elif loxi_utils.class_is_meter_band(self.c_name):
            return ("meterband", "OFMeterBand")
        else:
            return ("", None)

    @property
    @memoize
    def members(self):
        all_versions = []
        member_map = collections.OrderedDict()

        for (version, of_class) in self.version_map.items():
            for of_member in of_class.members:
                if isinstance(of_member, OFLengthMember) or \
                   isinstance(of_member, OFFieldLengthMember) or \
                   isinstance(of_member, OFPadMember):
                    continue
                if of_member.name not in member_map:
                    member_map[of_member.name] = JavaMember.for_of_member(self, of_member)

        return member_map.values()

    @property
    @memoize
    def is_virtual(self):
        return self.name in model.virtual_interfaces

    @property
    def is_universal(self):
        return len(self.all_versions) == len(model.versions)

    @property
    @memoize
    def all_versions(self):
        return self.version_map.keys()

    def has_version(self, version):
        return version in self.version_map

    def versioned_class(self, version):
        return JavaOFClass(self, version, self.version_map[version])

    @property
    @memoize
    def versioned_classes(self):
        if self.is_virtual:
            return []
        else:
            return [ self.versioned_class(version) for version in self.all_versions ]

#######################################################################
### (Versioned) Classes
#######################################################################

class JavaOFClass(object):
    """ Models an OpenFlow Message class for the purpose of the java class.
        Version specific child of a JavaOFInterface
    """
    def __init__(self, interface, version, ir_class):
        self.interface = interface
        self.ir_class = ir_class
        self.c_name = self.ir_class.name
        self.version = version
        self.constant_name = self.c_name.upper().replace("OF_", "")
        self.package = "org.openflow.protocol.ver%s" % version.of_version
        self.generated = False

    @property
    @memoize
    def unit_test(self):
        return JavaUnitTest(self)

    @property
    def name(self):
        return "%sVer%s" % (self.interface.name, self.version.of_version)

    @property
    def variable_name(self):
        return self.name[3:]

    @property
    def length(self):
        if self.is_fixed_length:
            return self.min_length
        else:
            raise Exception("No fixed length for class %s, version %s" % (self.name, self.version))

    @property
    def min_length(self):
        id_tuple = (self.ir_class.name, self.version.int_version)
        return of_g.base_length[id_tuple] if id_tuple in of_g.base_length else -1

    @property
    def is_fixed_length(self):
        return (self.ir_class.name, self.version.int_version) in of_g.is_fixed_length

    def all_properties(self):
        return self.interface.members

    def get_member(self, name):
        for m in self.members:
            if m.name == name:
                return m

    @property
    @memoize
    def data_members(self):
        return [ prop for prop in self.members if prop.is_data ]

    @property
    @memoize
    def fixed_value_members(self):
        return [ prop for prop in self.members if prop.is_fixed_value ]

    @property
    @memoize
    def public_members(self):
        return [ prop for prop in self.members if prop.is_public ]

    @property
    @memoize
    def members(self):
        members = [ JavaMember.for_of_member(self, of_member) for of_member in self.ir_class.members ]
        return members

    def all_versions(self):
        return [ JavaOFVersion(int_version)
                 for int_version in of_g.unified[self.c_name]
                 if int_version != 'union' and int_version != 'object_id' ]

    def version_is_inherited(self, version):
        return 'use_version' in of_g.unified[self.ir_class.name][version.int_version]

    def inherited_from(self, version):
        return JavaOFVersion(of_g.unified[self.ir_class.name][version.int_version]['use_version'])

    @property
    def is_virtual(self):
        return type_maps.class_is_virtual(self.c_name)

    @property
    def is_extension(self):
        return type_maps.message_is_extension(self.c_name, -1)

#######################################################################
### Member
#######################################################################


class JavaMember(object):
    """ Models a property (member) of an openflow class. """
    def __init__(self, msg, name, java_type, member):
        self.msg = msg
        self.name = name
        self.java_type = java_type
        self.member = member
        self.c_name = self.member.name if(hasattr(self.member, "name")) else ""

    @property
    def title_name(self):
        return self.name[0].upper() + self.name[1:]

    @property
    def constant_name(self):
        return self.c_name.upper()

    @property
    def default_name(self):
        if self.is_fixed_value:
            return self.constant_name
        else:
            return "DEFAULT_"+self.constant_name

    @property
    def default_value(self):
        java_type = self.java_type.public_type;

        if self.is_fixed_value:
            return self.enum_value
        elif re.match(r'List.*', java_type):
            return "Collections.emptyList()"
        elif java_type == "boolean":
            return "false";
        elif java_type in ("byte", "char", "short", "int", "long"):
            return "({0}) 0".format(java_type);
        else:
            return "null";

    @property
    def enum_value(self):
        if self.name == "version":
            return "OFVersion.%s" % self.msg.version.constant_version

        java_type = self.java_type.public_type;
        try:
            global model
            enum = model.enum_by_name(java_type)
            entry = enum.entry_by_version_value(self.msg.version, self.value)
            return "%s.%s" % ( enum.name, entry.name)
        except KeyError, e:
            print e.message
            return self.value

    @property
    def is_pad(self):
        return isinstance(self.member, OFPadMember)

    def is_type_value(self, version=None):
        if(version==None):
            return any(self.is_type_value(version) for version in self.msg.all_versions)
        try:
            return self.c_name in get_type_values(self.msg.c_name, version.int_version)
        except:
            return False

    @property
    def is_field_length_value(self):
        return isinstance(self.member, OFFieldLengthMember)

    @property
    def is_length_value(self):
        return isinstance(self.member, OFLengthMember)

    @property
    def is_public(self):
        return not (self.is_pad or self.is_length_value)

    @property
    def is_data(self):
        return isinstance(self.member, OFDataMember) and self.name != "version"

    @property
    def is_fixed_value(self):
        return hasattr(self.member, "value") or self.name == "version" \
                or ( self.name == "length" and self.msg.is_fixed_length) \
                or ( self.name == "len" and self.msg.is_fixed_length)

    @property
    def value(self):
        if self.name == "version":
            return self.msg.version.int_version
        elif self.name == "length" or self.name == "len":
            return self.msg.length
        elif self.java_type.public_type in ("int", "short", "byte") and self.member.value > 100:
            return "0x%x" % self.member.value
        else:
            return self.member.value

    @property
    def is_writeable(self):
        return self.is_data and not self.name in model.write_blacklist[self.msg.name]

    def get_type_value_info(self, version):
        return get_type_values(msg.c_name, version.int_version)[self.c_name]

    @property
    def length(self):
        if hasattr(self.member, "length"):
            return self.member.length
        else:
            count, base = loxi_utils.type_dec_to_count_base(self.member.type)
            return of_g.of_base_types[base]['bytes'] * count

    @staticmethod
    def for_of_member(java_class, member):
        if isinstance(member, OFPadMember):
            return JavaMember(None, "", None, member)
        else:
            if member.name == 'len':
                name = 'length'
            else:
                name = java_type.name_c_to_camel(member.name)
            j_type = java_type.convert_to_jtype(java_class.c_name, member.name, member.oftype)
            return JavaMember(java_class, name, j_type, member)

    @property
    def is_universal(self):
        if not self.msg.c_name in of_g.unified:
            print("%s not self.unified" % self.msg.c_name)
            return False
        for version in of_g.unified[self.msg.c_name]:
            if version == 'union' or version =='object_id':
                continue
            if 'use_version' in of_g.unified[self.msg.c_name][version]:
                continue

            if not self.member.name in (f['name'] for f in of_g.unified[self.msg.c_name][version]['members']):
                return False
        return True

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if other is None or type(self) != type(other):
            return False
        return (self.name,) == (other.name,)


#######################################################################
### Unit Test
#######################################################################

class JavaUnitTest(object):
    def __init__(self, java_class):
        self.java_class = java_class
        self.data_file_name = "of{version}/{name}.data".format(version=java_class.version.of_version,
                                                     name=java_class.c_name[3:])
    @property
    def package(self):
        return self.java_class.package

    @property
    def name(self):
        return self.java_class.name + "Test"

    @property
    def has_test_data(self):
        return test_data.exists(self.data_file_name)

    @property
    @memoize
    def test_data(self):
        return test_data.read(self.data_file_name)


#######################################################################
### Enums
#######################################################################

class JavaEnum(object):
    def __init__(self, c_name, version_enum_map):
        self.c_name = c_name
        self.name   = "OF" + java_type.name_c_to_caps_camel("_".join(c_name.split("_")[1:]))

        # Port_features has constants that start with digits
        self.name_prefix = "PF_" if self.name == "OFPortFeatures" else ""

        self.version_enums = version_enum_map

        entry_name_version_value_map = OrderedDefaultDict(lambda: OrderedDict())
        for version, ir_enum in version_enum_map.items():
            for ir_entry in ir_enum.entries:
                if "virtual" in ir_entry.params:
                    continue
                entry_name_version_value_map[ir_entry.name][version] = ir_entry.value

        self.entries = [ JavaEnumEntry(self, name, version_value_map)
                         for (name, version_value_map) in entry_name_version_value_map.items() ]

        self.entries = [ e for e in self.entries if e.name not in model.enum_entry_blacklist[self.name] ]
        self.package = "org.openflow.protocol"

    def wire_type(self, version):
        ir_enum = self.version_enums[version]
        if "wire_type" in ir_enum.params:
            return java_type.convert_enum_wire_type_to_jtype(ir_enum.params["wire_type"])
        else:
            return java_type.u8

    @property
    def versions(self):
        return self.version_enums.keys()

    @memoize
    def entry_by_name(self, name):
        try:
            return find(self.entries, lambda e: e.name == name)
        except KeyError:
            raise KeyError("Enum %s: no entry with name %s" % (self.name, name))

    @memoize
    def entry_by_c_name(self, name):
        try:
            return find(self.entries, lambda e: e.c_name == name)
        except KeyError:
            raise KeyError("Enum %s: no entry with c_name %s" % (self.name, name))

    @memoize
    def entry_by_version_value(self, version, value):
        try:
            return find(self.entries, lambda e: e.values[version] == value if version in e.values else False )
        except KeyError:
            raise KeyError("Enum %s: no entry with version %s, value %s" % (self.name, version, value))

# values: Map JavaVersion->Value
class JavaEnumEntry(object):
    def __init__(self, enum, name, values):
        self.enum = enum
        self.name = enum.name_prefix + "_".join(name.split("_")[1:]).upper()
        self.values = values

    def has_value(self, version):
        return version in self.values

    def value(self, version):
        return self.values[version]

    def format_value(self, version):
        res = self.enum.wire_type(version).format_value(self.values[version])
        return res

    def all_values(self, versions, not_present=None):
        return [ self.values[version] if version in self.values else not_present for version in versions ]
