Try out the Language Server Protocol for Kakoune!

Clone this repo, decorate your kakrc like so:

```kak
set-option -add global completers option=lsp_completions

# Commands for language servers
decl str lsp_python_cmd pyls
decl str lsp_typescript_cmd 'node javascript-typescript-langserver/lib/language-server-stdio.js'
decl str lsp_go_cmd 'go-langserver'

# Some keybindings
map global user . ':lsp_goto_definition<ret>'
map global user u ':lsp_references<ret>'
map global user h ':lsp_hover docsclient<ret>'
map global user i ':lsp_hover info<ret>'

# Hover and diagnostics on idle
hook -group lsp global NormalIdle .* %{
    lsp_diagnostics cursor
    lsp_hover cursor
}

# Gutter
hook global WinCreate .* %{
    try %{
        add-highlighter flag_lines default lsp_flags
    }
}
```

Then attach `lspc.py` to a running kak process. Say it has PID 4032 (you see
this in the lower right corner), then issue:

    python lspc.py 4032

And hope something works!
