import os
current_path = __file__
prev_path =  os.path.abspath(os.path.join(current_path, os.pardir))
template_path =  os.path.abspath(os.path.join(prev_path, os.pardir))
def plugin_settings(settings):
    settings.MAKO_TEMPLATE_DIRS_BASE.append(template_path + '/templates')
    settings.EOL_COMPLETION_TIME_CACHE = 300
    settings.EOL_COMPLETION_LIMIT_STUDENT = 3000