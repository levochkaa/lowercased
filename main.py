import glob
import os
from shutil import move

temp_path = "temp"
en_dir = "en"
lowercased_dir = "lowercased-en"

# (file_prefix_in_en_dir, output_filename, format, xml_start_tag)
TARGETS = [
    ("android_en_v",   "android.xml",    "xml", '    <string name="'),
    ("android_x_en_v", "androidx.xml",    "xml", '  <string name='),
    ("tdesktop_en_v",  "desktop.strings", "strings", None),
    ("ios_en_v",       "ios.strings",     "strings", None),
    ("macos_en_v",     "macos.strings",   "strings", None),
    ("unigram_en_v",   "unigram.xml",     "xml", '    <string name="'),
    ("weba_en_v",      "weba.strings",    "strings", None),
    ("webk_en_v",      "webk.strings",    "strings", None),
]


def find_file(prefix):
    """Find a file in en/ that starts with the given prefix."""
    pattern = os.path.join(en_dir, f"{prefix}*")
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No file found matching {pattern}")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple files found matching {pattern}: {matches}")
    return matches[0]


def process_xml(en_path, lowercased_path, start):
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith(start) and line.endswith('</string>\n'):
                splitted = line.split(start)
                other_split = splitted[1].split('">')
                new_line = f'{start}{other_split[0]}">{other_split[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_path)


def process_strings(en_path, lowercased_path):
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                if len(splitted) == 2:
                    new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                    temp_file.write(new_line)
                else:
                    temp_file.write(line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_path)


if __name__ == "__main__":
    for file_prefix, output_name, fmt, xml_start in TARGETS:
        en_path = find_file(file_prefix)
        lowercased_path = os.path.join(lowercased_dir, output_name)
        print(f"{os.path.basename(en_path)} -> {lowercased_path}")
        if fmt == "xml":
            process_xml(en_path, lowercased_path, xml_start)
        else:
            process_strings(en_path, lowercased_path)
