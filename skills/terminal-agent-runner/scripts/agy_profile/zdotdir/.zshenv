# zsh reads this first in every shell under an agy profile: the worker's tab, and each
# shell agy starts for a command. Those shells inherit agy's account HOME; put the real
# one back before anything else reads it, then continue with the user's own startup files.
if [[ -n ${AGY_REAL_HOME:-} && $HOME != $AGY_REAL_HOME ]]; then
  export HOME=$AGY_REAL_HOME
fi
[[ -f ${AGY_REAL_ZDOTDIR:-$HOME}/.zshenv ]] && source ${AGY_REAL_ZDOTDIR:-$HOME}/.zshenv
