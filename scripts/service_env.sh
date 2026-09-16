#!/usr/bin/env bash
# A data-only dotenv reader. Keep its grammar in sync with service_env.ps1.
# Internal names are reserved so file assignments cannot overwrite parser state.

_audio_intel_env_error() {
  printf 'Invalid environment file %s:%s (%s)\n' "$_ai_file" "$_ai_line_number" "$1" >&2
  return 1
}

_audio_intel_env_value() {
  local _ai_raw="$1" _ai_quote='' _ai_closed=0 _ai_ch _ai_next _ai_tail _ai_ref
  local _ai_pos=0
  _ai_value=''
  if [[ "${_ai_raw:0:1}" == '"' || "${_ai_raw:0:1}" == "'" ]]; then
    _ai_quote="${_ai_raw:0:1}"
    _ai_pos=1
  else
    _ai_raw="${_ai_raw%"${_ai_raw##*[!$' \t']}"}"
  fi
  while (( _ai_pos < ${#_ai_raw} )); do
    _ai_ch="${_ai_raw:_ai_pos:1}"
    _ai_next="${_ai_raw:_ai_pos+1:1}"
    if [[ -n "$_ai_quote" && "$_ai_ch" == "$_ai_quote" ]]; then
      _ai_tail="${_ai_raw:_ai_pos+1}"
      [[ "$_ai_tail" =~ ^[[:blank:]]*(#.*)?$ ]] || { _audio_intel_env_error 'text after closing quote'; return 1; }
      _ai_closed=1
      break
    fi
    if [[ -z "$_ai_quote" ]]; then
      if [[ "$_ai_ch" == '#' ]] && { (( _ai_pos == 0 )) || [[ "${_ai_raw:_ai_pos-1:1}" == ' ' || "${_ai_raw:_ai_pos-1:1}" == $'\t' ]]; }; then
        _ai_tail="${_ai_raw:0:_ai_pos}"
        _ai_tail="${_ai_tail##*[!$' \t']}"
        _ai_value="${_ai_value:0:${#_ai_value}-${#_ai_tail}}"
        break
      fi
      [[ "$_ai_ch" != '"' && "$_ai_ch" != "'" ]] || { _audio_intel_env_error 'quote must enclose the whole value'; return 1; }
    fi
    if [[ "$_ai_quote" == '"' && "$_ai_ch" == '\' && ( "$_ai_next" == '"' || "$_ai_next" == '$' || "$_ai_next" == '\' ) ]]; then
      _ai_value+="$_ai_next"
      _ai_pos=$((_ai_pos + 2))
      continue
    fi
    if [[ "$_ai_quote" != "'" ]]; then
      if [[ "$_ai_ch" == '`' || ( "$_ai_ch" == '$' && "$_ai_next" == '(' ) ]]; then
        _audio_intel_env_error 'command substitution is not supported'; return 1
      fi
      if [[ "$_ai_ch" == '$' ]]; then
        _ai_tail="${_ai_raw:_ai_pos+1}"
        if [[ "$_ai_next" == '{' ]]; then
          [[ "$_ai_tail" =~ ^\{([a-zA-Z_][a-zA-Z0-9_]*)\} ]] || { _audio_intel_env_error 'invalid variable reference'; return 1; }
        elif [[ ! "$_ai_tail" =~ ^([a-zA-Z_][a-zA-Z0-9_]*) ]]; then
          _ai_value+='$'
          _ai_pos=$((_ai_pos + 1))
          continue
        fi
        _ai_ref="${BASH_REMATCH[1]}"
        _ai_pos=$((_ai_pos + 1 + ${#BASH_REMATCH[0]}))
        _ai_value+="${_ai_values[$_ai_ref]-}"
        continue
      fi
    fi
    _ai_value+="$_ai_ch"
    _ai_pos=$((_ai_pos + 1))
  done
  if [[ -n "$_ai_quote" ]]; then
    (( _ai_closed == 1 )) || { _audio_intel_env_error 'unclosed quote'; return 1; }
  fi
  return 0
}

load_service_environment() {
  local _ai_file="$1" _ai_line_number=0 _ai_line _ai_key _ai_raw _ai_value
  local -A _ai_inherited=() _ai_values=() _ai_assignments=() _ai_lines=()
  SERVICE_ENV_SOURCE='default (no .env)'
  if [[ "${AUDIO_INTEL_LOAD_ENV:-1}" == 0 ]]; then
    SERVICE_ENV_SOURCE='disabled (AUDIO_INTEL_LOAD_ENV=0)'
    return 0
  fi
  [[ -e "$_ai_file" ]] || return 0
  [[ -f "$_ai_file" && -r "$_ai_file" ]] || { _audio_intel_env_error 'not a readable file'; return 1; }
  while IFS= read -r _ai_key; do
    _ai_inherited["$_ai_key"]=1
    _ai_values["$_ai_key"]="${!_ai_key}"
  done < <(compgen -e)
  while IFS= read -r _ai_line || [[ -n "$_ai_line" ]]; do
    _ai_line_number=$((_ai_line_number + 1))
    if (( _ai_line_number == 1 )); then _ai_line="${_ai_line#$'\xef\xbb\xbf'}"; fi
    _ai_line="${_ai_line%$'\r'}"
    _ai_line="${_ai_line#"${_ai_line%%[!$' \t']*}"}"
    [[ -z "$_ai_line" || "${_ai_line:0:1}" == '#' ]] && continue
    if [[ "$_ai_line" =~ ^export[[:blank:]]+ ]]; then _ai_line="${_ai_line:${#BASH_REMATCH[0]}}"; fi
    [[ "$_ai_line" =~ ^([a-zA-Z_][a-zA-Z0-9_]*)[[:blank:]]*=[[:blank:]]*(.*)$ ]] || { _audio_intel_env_error 'expected KEY=value'; return 1; }
    _ai_key="${BASH_REMATCH[1]}"; _ai_raw="${BASH_REMATCH[2]}"
    [[ "$_ai_key" != _ai_* && "$_ai_key" != AUDIO_INTEL_LOAD_ENV ]] || { _audio_intel_env_error 'reserved variable'; return 1; }
    _audio_intel_env_value "$_ai_raw" || return 1
    if [[ -z "${_ai_inherited[$_ai_key]+present}" ]]; then
      _ai_values["$_ai_key"]="$_ai_value"
      _ai_assignments["$_ai_key"]="$_ai_value"
      _ai_lines["$_ai_key"]="$_ai_line_number"
    fi
  done < "$_ai_file"
  # Validate the whole file before changing the process environment.
  for _ai_key in "${!_ai_assignments[@]}"; do
    _ai_line_number="${_ai_lines[$_ai_key]}"
    export "$_ai_key=${_ai_assignments[$_ai_key]}" 2>/dev/null || { _audio_intel_env_error 'cannot export variable'; return 1; }
  done
  SERVICE_ENV_SOURCE="$_ai_file (external environment takes precedence)"
}
