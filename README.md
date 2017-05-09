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
decl str lsp-python-disabled-diagnostics '^E501'

# Example keybindings
map -docstring %{Goto definition}     global user . ':lsp-goto-definition<ret>'
map -docstring %{Select references}   global user r ':lsp-references<ret>'
map -docstring %{Hover help}          global user h ':lsp-hover docsclient<ret>'
map -docstring %{Next diagnostic}     global user j ':lsp-diagnostics next cursor<ret>'
map -docstring %{Previous diagnostic} global user k ':lsp-diagnostics prev cursor<ret>'

# Manual completion and signature help when needed
map global insert <a-c> '<a-;>:eval -draft %(exec b; lsp-complete)<ret>'
map global insert <a-h> '<a-;>:lsp-signature-help<ret>'

# Hover and diagnostics on idle
hook -group lsp global NormalIdle .* %{
    lsp-diagnostics cursor
    lsp-hover cursor
}

# Aggressive diagnostics
hook -group lsp global InsertEnd .* lsp-sync
```

Then attach `lspc.py` to a running kak process. Say it has PID 4032 (you see
this in the lower right corner), then issue:

    python lspc.py 4032

And hope something works!

