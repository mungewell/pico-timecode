setting = {
    'framerate' : ['30', ['30', '29.97', '25', '24.98', '24', '23.98']],
    'dropframe' : ['No', ['No', 'Yes']],
    'output'    : ['Line', ['Mic', 'Line']],
    'flashframe': ['11', ['Off', '0', '11']],
    'userbits'  : ['Text', ['Text', 'Digits', 'Date']],
    'powersave' : ['No', ['No', 'Yes']],
    'zoom'      : ['No', ['No', 'Yes']],
    'monitor'   : ['No', ['No', 'Yes']],
    'calibrate' : ['No', ['No', 'Once', 'Always']],
    'ub_ascii'  : "PICO",
    'ub_bcd'    : "00000000",
    'ub_date'   : "Y74-M01-D01+0000",
}

#################################################
###  micropython far too simple config file   ###
###  do not write below this header           ###
###  do not store collections                 ### 
###  usage (add or overwrite config):         ###
### >>>import config                          ###
### >>>config.set('mftsc','I','exist')##
###  usage (access config):                   ###
### >>>config.mftsc['I']                      ###
### 'exist'                                   ###
#################################################
import gc
import sys

def set(dictname, key, value, do_reload=True):
    newrow = _key_value_dict(key,value)
    me = _open_file_to_lines()
    dict_start = -1
    dict_end = -1
    new_dict = False
    linx = -1
    for rowx, linr in enumerate(me):
        if '#######################################' in linr:
            new_dict = True
            break
        if linr[:4] == '    ':
            linr = '    ' + ' '.join(linr.split()) + '\n'
        else:
            linr = ' '.join(linr.split()) + '\n'
        if linr[:len(dictname)+4] == dictname + ' = {':
            dict_start = rowx
        if dict_start != -1:
            if "    '" + str(key) + "' :" in linr:
                linx = rowx
                break
            if linr == '}\n':
                dict_end = rowx
                break
    result = 0
    if new_dict:
        newfilerows = _new_dict(dictname,key,value) + me
        result = _write_lines_to_file(newfilerows)    
    elif linx != -1:
        me[linx] = newrow
        result = _write_lines_to_file(me)
    elif dict_end:
        me.insert(dict_end,newrow)
        result = _write_lines_to_file(me)
    if do_reload:
        if result:
            _reload()
        else:
            return
    else:
        return

def _reload():
    del sys.modules['libs.config']
    # if config is imported by other modules, delete it recursively
    for mo in sys.modules:
        if 'config' in dir(sys.modules[mo]):
            del sys.modules[mo].__dict__['config']
            sys.modules[mo].__dict__['config'] = __import__('libs.config').config
    gc.collect()
    sys.modules['libs.config'] = __import__('libs.config').config

def _new_dict(dictname,key,value):
    return [dictname + ' = {\n',
    _key_value_dict(key,value),
    '}\n']

def _key_value_dict(key,value):
    if isinstance(value,str):
        return "    '" + str(key) + "' : '" + value + "',\n"
    else:
        return "    '" + str(key) + "' : " + str(value) + ",\n"
    
def _write_lines_to_file(lines):
    try:
        with open(__file__, 'w') as f:
            for line in lines:
                f.write(line)
            return 1
    except Exception:
        print("Could not write file: ", __file__)
        return 0

def _open_file_to_lines():
    conf_lines = []
    try:
        with open(__file__, 'r') as f:
            conf_lines = f.readlines()
    except Exception:
        print("Could not read file: ", __file__)
    return conf_lines
