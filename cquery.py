from lspc import client
import utils
import libkak
import sys
from enum import IntEnum, unique


@unique
class SymbolKind(IntEnum):
    Invalid = 0
    File = 1
    Type = 2
    Func = 3
    Var = 4


@unique
class StorageClass(IntEnum):
    Invalid = 0
    No = 1
    Extern = 2
    Static = 3
    PrivateExtern = 4
    Auto = 5
    Register = 6


@unique
class SemanticSymbolKind(IntEnum):
    Unknown = 0
    File = 1
    Module = 2
    Namespace = 3
    Package = 4

    Class = 5
    Method = 6
    Property = 7
    Field = 8
    Constructor = 9

    Enum = 10
    Interface = 11
    Function = 12
    Variable = 13
    Constant = 14

    String = 15
    Number = 16
    Boolean = 17
    Array = 18
    Object = 19

    Key = 20
    Null = 21
    EnumMember = 22
    Struct = 23
    Event = 24

    Operator = 25
    TypeParameter = 26

    TypeAlias = 252
    Parameter = 253
    StaticMethod = 254
    Macro = 255


def face_for_symbol(symbol):
    if symbol['kind'] == SemanticSymbolKind.Class or symbol['kind'] == SemanticSymbolKind.Struct:
        return 'cqueryTypes'
    elif symbol['kind'] == SemanticSymbolKind.Enum:
        return 'cqueryEnums'
    elif symbol['kind'] == SemanticSymbolKind.TypeAlias:
        return 'cqueryTypeAliases'
    elif symbol['kind'] == SemanticSymbolKind.TypeParameter:
        return 'cqueryTemplateParameters'
    elif symbol['kind'] == SemanticSymbolKind.Function:
        return 'cqueryFreeStandingFunctions'
    elif symbol['kind'] == SemanticSymbolKind.Method or symbol['kind'] == SemanticSymbolKind.Constructor:
        return 'cqueryMemberFunctions'
    elif symbol['kind'] == SemanticSymbolKind.StaticMethod:
        return 'cqueryStaticMemberFunctions'
    elif symbol['kind'] == SemanticSymbolKind.Variable:
        if symbol['parentKind'] == SymbolKind.Func:
            return 'cqueryFreeStandingVariables'

        return 'cqueryGlobalVariables'
    elif symbol['kind'] == SemanticSymbolKind.Field:
        if symbol['storage'] == StorageClass.Static:
            return 'cqueryStaticMemberVariables'

        return 'cqueryMemberVariables'
    elif symbol['kind'] == SemanticSymbolKind.Parameter:
        return 'cqueryParameters'
    elif symbol['kind'] == SemanticSymbolKind.EnumMember:
        return 'cqueryEnumConstants'
    elif symbol['kind'] == SemanticSymbolKind.Namespace:
        return 'cqueryNamespaces'
    elif symbol['kind'] == SemanticSymbolKind.Macro:
        return 'cqueryMacros'


@client.message_handler_named("$cquery/publishSemanticHighlighting")
def cquery_publishSemanticHighlighting(filetype, params):
    print("published semantic highlighting")
    buffile = utils.uri_to_file(params['uri'])
    clientp = client.client_editing.get((filetype, buffile))
    if not clientp:
        return
    r = libkak.Remote.onclient(client.session, clientp, sync=False)
    r.arg_config['disabled'] = (
        'kak_opt_lsp_' + filetype + '_disabled_sem_hl',
        libkak.Args.string)

    @r
    def _(timestamp, pipe, disabled):
        flags = [str(timestamp)]

        for hl in params['symbols']:
            face = face_for_symbol(hl)
            if face is None:
                continue
            for range in hl['ranges']:
                (line0, col0), (line1, col1) = utils.range(range)
                flags.append("{}.{},{}.{}|{}".format(line0, col0, line1, col1, face))

        # todo:Set for the other buffers too (but they need to be opened)
        msg = 'try %{add-highlighter buffer/ ranges cquery_semhl}\n'
        msg += 'set buffer=' + buffile + ' cquery_semhl '
        msg += utils.single_quoted(':'.join(flags))
        print(msg)
        pipe(msg)


if __name__ == '__main__':
    client.main(sys.argv[1], messages="""
        try %{declare-option range-specs cquery_semhl}
        """)
