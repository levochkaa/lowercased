from shutil import move

temp_path = "temp"


def android():
    en_path = "en/android.xml"
    lowercased_en_path = "lowercased-en/android.xml"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        start = '    <string name="'
        for line in input_file:
            if line.startswith(start) and line.endswith('</string>\n'):
                splitted = line.split(start)
                other_split = splitted[1].split('">')
                new_line = f'{start}{other_split[0]}">{other_split[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def androidx():
    en_path = "en/androidx.xml"
    lowercased_en_path = "lowercased-en/androidx.xml"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        start = '  <string name='
        for line in input_file:
            if line.startswith(start) and line.endswith('</string>\n'):
                splitted = line.split(start)
                other_split = splitted[1].split('">')
                new_line = f'{start}{other_split[0]}">{other_split[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def desktop():
    en_path = "en/desktop.strings"
    lowercased_en_path = "lowercased-en/desktop.strings"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def ios():
    en_path = "en/ios.strings"
    lowercased_en_path = "lowercased-en/ios.strings"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def macos():
    en_path = "en/macos.strings"
    lowercased_en_path = "lowercased-en/macos.strings"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def unigram():
    en_path = "en/unigram.xml"
    lowercased_en_path = "lowercased-en/unigram.xml"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        start = '    <string name="'
        for line in input_file:
            if line.startswith(start) and line.endswith('</string>\n'):
                splitted = line.split(start)
                other_split = splitted[1].split('">')
                new_line = f'{start}{other_split[0]}">{other_split[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def weba():
    en_path = "en/weba.strings"
    lowercased_en_path = "lowercased-en/weba.strings"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


def webk():
    en_path = "en/webk.strings"
    lowercased_en_path = "lowercased-en/webk.strings"
    with open(en_path, "r") as input_file, open(temp_path, "w") as temp_file:
        for line in input_file:
            if line.startswith('"') and line.endswith('";\n'):
                splitted = line.split('" = "')
                new_line = f'{splitted[0]}" = "{splitted[1].lower()}'
                temp_file.write(new_line)
            else:
                temp_file.write(line)
    move(temp_path, lowercased_en_path)


if __name__ == "__main__":
    android()
    androidx()
    desktop()
    ios()
    macos()
    unigram()
    weba()
    webk()
