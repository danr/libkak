Try out the Language Server Protocol for Kakoune!

Clone this repo, decorate your kakrc like so:

```kak
# Commands for language servers
decl str lsp_servers %{
    python:pyls
    typescript:node javascript-typescript-langserver/lib/language-server-stdio.js
    javascript:node javascript-typescript-langserver/lib/language-server-stdio.js
    go:go-langserver
}

# Ignore E501 for python (Line length > 80 chars)
decl str lsp_python_disabled_diagnostics '^E501'

# Example keybindings
map -docstring %{Goto definition}     global user . ':lsp_goto_definition<ret>'
map -docstring %{Select references}   global user r ':lsp_references<ret>'
map -docstring %{Hover help}          global user h ':lsp_hover docsclient<ret>'
map -docstring %{Next diagnostic}     global user j ':lsp_diagnostics next cursor<ret>'
map -docstring %{Previous diagnostic} global user k ':lsp_diagnostics prev cursor<ret>'

# Manual completion and signature help when needed
map global insert <a-c> '<a-;>:eval -draft %(exec b; lsp_complete)<ret>'
map global insert <a-h> '<a-;>:lsp_signature_help<ret>'

# Hover and diagnostics on idle
hook -group lsp global NormalIdle .* %{
    lsp_diagnostics cursor
    lsp_hover cursor
}

# Aggressive diagnostics
hook -group lsp global InsertEnd .* lsp_sync
```

Then attach `lspc.py` to a running kak process. Say it has PID 4032 (you see
this in the lower right corner), then issue:

    python lspc.py 4032

And hope something works!

