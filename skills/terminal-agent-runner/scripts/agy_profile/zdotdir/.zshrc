# Hand ZDOTDIR back before the user's .zshrc runs: tools such as compinit write their
# caches under it, and this folder is installed read-only. zsh reads .zlogin from the
# restored location too.
if [[ -n ${AGY_REAL_ZDOTDIR:-} ]]; then
  export ZDOTDIR=$AGY_REAL_ZDOTDIR
else
  unset ZDOTDIR
fi
# /etc/zshrc already pointed history at this folder; keep it in the user's own file.
HISTFILE=${ZDOTDIR:-$HOME}/.zsh_history
[[ -f ${ZDOTDIR:-$HOME}/.zshrc ]] && source ${ZDOTDIR:-$HOME}/.zshrc
# A runner worker's tab sets AGY_SHIM_DIR so `agy` resolves to the profile wrapper. It
# goes last because the user's .zshrc may put the real agy's directory first.
if [[ -n ${AGY_SHIM_DIR:-} ]]; then
  path=($AGY_SHIM_DIR $path)
fi
