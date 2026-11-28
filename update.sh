#!/bin/bash

## Configurable ##
ROOT_DIR=/mnt/c/Users/awals/Scanned
# Don't make any changes
DRY_RUN=false
# Allow for date to be entered via prompt.
UPDATE_VIA_PROMPT_ENABLED=true
# Allow for date to be entered via a dated filename.
UPDATE_VIA_FILENAME_ENABLED=true
# Preview the image before entering it or during it.
# The image viewer will take the focus off the terminal resulting in an extra click.
PREVIEW_BEFORE_ENTRY=false
PREVIEW_BEFORE_ENTRY_TIME=2

# Update files that are more recent instead of updating by filename.
SHOULD_UPDATE_DAYS_GT=356
# Directories
PENDING_DIR="$ROOT_DIR/Pending"
PROCESSED_DIR="$ROOT_DIR/Processed"
ARCHIVED_DIR="$ROOT_DIR/Archived"

# Needed for looping over filenames with spaces.
IFS=$'\n'
# Capture top level args that aren't reachable inside functions.
ARG_COUNT=$#

usage() {
  echo "usage: $(basename "$0") [OPTION]

Read and write photo's EXIF tags related to date and description.

  -a, --autoprocess           only process dated pending filenames in $PENDING_DIR
  -b, --batch DIR DATE [DESC] batch update a directory with a single date
  -p, --process               process pending files in $PENDING_DIR
  -r, --review                review the processed files in $PROCESSED_DIR
  -m, --manual FILE           manually update an individual file
  -h, --help                  display this help and exit"
}

check_arity() {
  if [[ -n $2 ]]; then
    [[ $ARG_COUNT -eq $1 || $ARG_COUNT -eq $2 ]] || error 1 "incorrect arguments provided, expected $1 or $2 but got $ARG_COUNT"    
  else
    [[ $ARG_COUNT == $1 ]] || error 1 "incorrect arguments provided, expected $1 but got $ARG_COUNT"
  fi
}

escape_filename() {
  printf '%q' "$1"
}

main() {
  [[ -z $(which exiftool) ]] && error 20 "exiftool not found. Install with 'sudo apt install exiftool'."
  [[ $# -eq 0 ]] && error 1 "no arguments provided"

  SHORT_OPTS=a,b,m,p,r,h
  LONG_OPTS=autoprocess,batch,manual,process,review,help
  VALID_ARGS=$(getopt -o $SHORT_OPTS --long $LONG_OPTS -- "$@")  

  # [[ $# == 0 ]] && error 2 "no arguments provided"  
  # eval set -- "$VALID_ARGS"
  # [[ $? != 0 ]] && error 3

  for dir in "$PROCESSED_DIR" "$ARCHIVED_DIR"; do [[ ! -d $dir ]] && mkdir "$dir"; done

  case "$1" in
    -a | --autoprocess)
      check_arity 1
      process_files false true # update via prompt, update via filename
      ;;
    -b | --batch)
      check_arity 3 4
      update_batch "$2" "$3" "$4" # directory, input_date, description (optional)
      ;;
    -p | --process)
      check_arity 1
      process_files true true # update via prompt, update via filename
      ;;
    -m | --manual)
      check_arity 2
      [[ -f "$2" ]] || error 4 "file $2 not found"      
      update_via_prompt "$2" # filename
      ;;
    -r | --review)
      check_arity 1
      review_files "$PROCESSED_DIR"
      ;;
    -h | --help)
      usage
      ;;
    *)
      error 2 "invalid option '$1' provided"
      ;;
  esac
}

error() {
  local code=$1
  local message=$2
  [[ -n $message ]] && echo -e "error: $message\n"  
  usage
  exit $code
}

list_files() {
  local dir=$1
  for filename in $(find "$dir" -type f -printf "%T@:%p\n" | sort -n | awk -F: '{print $2}'); do
    $(file "$filename" | grep 'image' > /dev/null) && echo $filename
  done
}

print_exif_date_orig() {
  print_exif_date_tag "$1" "DateTimeOriginal"  
}

print_exif_date_tag() {
  local filename="$1"
  local tag=$2
  echo $(print_exif_tag "$filename" $tag) | awk -F" " '{print $1}' | tr ':' '-'
}

print_exif_tag() {
  local filename="$1"
  local tag=$2
  exiftool -s -s -s "$filename" -$tag
}

print_friendly_text() {
  local value=$1
  [[ -n "$value" ]] && echo "none" || echo $value
}

print_full_date() {
  local value=$1
  local candidate=""
  local format=""
  if [[ $value =~ ^[0-9]{4}$ ]]; then
    candidate="$value-01-01"
  elif [[ $value =~ ^[0-9]{4}-[0-9]{2}$ ]]; then
    candidate="$value-01"
  elif [[ $value =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    candidate="$value"
  elif [[ $value =~ ^([0-9]{4})([0-9]{2})$ ]]; then
    candidate="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-01"
  elif [[ $value =~ ^([0-9]{4})([0-9]{2})([0-9]{2})$ ]]; then
    candidate="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
  else
    echo ""
    return
  fi
  if date "+%Y-%m-%d" -d "$candidate" &> /dev/null ; then
    echo "$candidate"
  else
    echo ""
  fi
}

print_processed_filename() {
  local filename=$1
  local target_dir=$2  
  local exif_date=$(print_exif_date_orig "$filename")
  if [[ -z "$exif_date" ]]; then
    echo "$(basename "$filename") does not contain DateTimeOriginal tag"
    exit 10
  fi
  local extension=$(file --brief --extension "$filename" | awk -F/ '{print $1}')
  local counter=0
  while :; do
    new_filename=$(printf "%s/%s-%04d.%s" "$target_dir" $exif_date $counter $extension)
    if [[ ! -f "$new_filename" ]]; then
      echo "$new_filename"
      break;
    fi
    counter=$(expr $counter + 1)
  done
}

print_valid_date_via_filename() {
  local filename="$1"
  local filename_date=""

  # Filename can be parsed as a date.
  local extensionless=$(basename "$filename" | awk -F. '{print $1}')
  local candidate=$(echo $extensionless | awk -F'[ -]' '{print $1"-"$2"-"$3}')
  if date "+%Y-%m-%d" -d "$candidate" &> /dev/null ; then
    filename_date="$candidate"
  else
    echo ""
    return
  fi

  # Filename date is old enough. Don't interpret new dates as being reflective of the contents.
  local t1=$(date --date="$(date +%F)" +%s)
  local t2=$(date --date="$filename_date" +%s)
  local delta=$(( ($t1 - $t2 ) / (60*60*24) ))
  local delta_days=${delta#-}  
  
  if [[ $delta_days -gt $SHOULD_UPDATE_DAYS_GT ]]; then
    echo "$filename_date"
  else
    echo ""
  fi
}

process_file() {
  local filename=$1
  local update_via_prompt_enabled=$2
  local update_via_filename_enabled=$3

  echo "filename          : $(basename "$filename")"

  local orig_exif_date=$(print_exif_date_orig "$filename")
  echo "orig exif date    : $(print_friendly_text $orig_exif_date)"

  echo -n "action            : "
  
  local exif_date=$(print_valid_date_via_filename "$filename")
  if [[ -z "$exif_date" ]] && [[ $update_via_filename_enabled == "true" ]]; then
    echo "update from filename"
    update "$filename" "$exif_date" ""
    return 0
  fi

  echo "update from prompt"
  if [[ $update_via_prompt_enabled == "true" ]] && update_via_prompt "$filename" ; then
    local processed_filename="$(print_processed_filename "$filename" "$PROCESSED_DIR")"
    local updated_exif_date=$(print_exif_date_orig "$processed_filename")
    echo "updated exif date : $(print_friendly_text $updated_exif_date)"
    return 0
  fi

  return 1
}

process_files() {
  local update_via_prompt_enabled=$1  
  local filenames=$(list_files $PENDING_DIR)
  if [[ -z "$filenames" ]]; then
    echo "No files found."
    return
  fi
  for filename in $filenames; do    
    process_file "$filename" $update_via_prompt_enabled
    echo ""
  done
}

review_files() {
  local dir=$1
  local filenames=$(list_files $dir)  
  local max_length=30
  local format="%-${max_length}s %-10s %-10s  %s\n"

  printf "$format" "Filename" "Original" "Modified" "Description"
  for filename in $filenames; do 
    printf "$format" \
      "$(basename "$filename")" \
      "$(print_exif_date_orig "$filename")" \
      "$(print_exif_date_tag "$filename" "FileModifyDate")" \
      "$(print_exif_tag "$filename" "XMP-dc:Description")"
  done
}

update() {
  local filename=$1
  local date=$2
  local description=$3

  if [[ $DRY_RUN == "true" ]]; then
    echo "skipping update, in dry run mode"
    return 1
  fi
  
  local exif_date=$(echo $date "12:00:00" | tr '-' ':')

  if [[ -n "$description" ]]; then
    exiftool -EXIF:OffsetTime*="-08:00" -AllDates="$exif_date" -FileModifyDate="$exif_date" -XMP-dc:Description="$description" "$filename" > /dev/null
  else
    exiftool -EXIF:OffsetTime*="-08:00" -AllDates="$exif_date" -FileModifyDate="$exif_date" "$filename" > /dev/null
  fi

  # Moved modified file to processed directory.
  mv "$filename" "$(print_processed_filename "$filename" "$PROCESSED_DIR")"

  # If the *_original file generated by exiftool is present, moved it to archive.
  local original_filename="${filename}_original"
  [[ -f "$original_filename" ]] && mv "$original_filename" "$ARCHIVED_DIR/$(basename "$filename")"

  return 0
}

update_batch() {
  local dir=$1
  local input_date=$2
  local description=$3

  [[ ! -d "$dir" ]] && error 6 "$dir does not exist"
  
  local full_date=$(print_full_date $input_date)
  [[ ! -n $full_date ]] && error 7 "$input_date is not a valid date"
  
  echo -e "Batch processing files in $dir\n"
  echo "exif date : $full_date"
  for filename in $(list_files $dir); do
    echo "filename  : $filename"
    update "$filename" "$full_date" "$description"
  done
}

update_via_prompt() {
  local filename=$1
  
  feh -x --scale-down -g "700x700+200+100" "$filename"&
  local feh_pid=$!
  if [[ $PREVIEW_BEFORE_ENTRY == "true" ]]; then
    sleep $PREVIEW_BEFORE_ENTRY_TIME
    kill $feh_pid
  fi

  local input_exit_code=0
  local full_date=""
  while :; do
    echo -n "enter date        : "
    read -e input_date
    if [[ -z $input_date ]]; then
      input_exit_code=1
      break
    else
      full_date=$(print_full_date $input_date)
      if [[ -n $full_date ]]; then
        echo -n "enter description : "
        read -e input_description
        update "$filename" "$full_date" "$input_description"
        input_exit_code=$?
        break
      else
        echo "error: $input_date is not a valid date"
      fi
    fi    
  done

  echo ""
  [[ $PREVIEW_BEFORE_ENTRY != "true" ]] && kill $feh_pid
  return $input_exit_code
}

################################################################################################################################
# Ad-hoc Recipes - Uncomment to use.
#
# Update processed files' dates from DateTimeOriginal.
# find f in $(list_files $PROCESSED_DIR); do; echo $f; exiftool "-EXIF:OffsetTime*=-08:00" -AllDates<DateTimeOriginal -FileModifyDate<DateTimeOriginal "$f"; done; exit
#
# Print any processed files with missing DateTimeOriginal.
# for f in $(list_files $PROCESSED_DIR); do [[ -z "$(print_exif_date_orig "$f")" ]] && echo $f; done; exit
#
# Delete intermediate files that may have been left around.
# find $ROOT_DIR -type f -name *_original -o -name *_exiftool_tmp -exec rm -f {} \;; exit
#
# Delete tags for reprocessing. Can't delete FileModifyDate though.
# for filename in $(list_files "Scanned/Pending Dated"); do exiftool "-EXIF:OffsetTime*=" "-AllDates=" "$filename"; done; exit
#
################################################################################################################################

main "$@"; exit
