from django.conf import settings
from django.utils.translation import ugettext_noop

from courseware.tabs import EnrolledTab
from xmodule.tabs import TabFragmentViewMixin
from lms.djangoapps.courseware.access import has_access


class EolCompletionTab(TabFragmentViewMixin, EnrolledTab):
    type = 'eol_completion'
    title = ugettext_noop('Seguimiento')
    priority = None
    view_name = 'completion_view'
    fragment_view_name = 'eol_completion.views.EolCompletionFragmentView'
    is_hideable = True
    is_default = True
    body_class = 'eol_completion'
    online_help_token = 'eol_completion'
    # True if this tab should be displayed only for instructors
    course_staff_only = True

    @classmethod
    def is_enabled(cls, course, user=None):
        """
        Returns true if the specified user has staff access.
        """
        return bool(user and has_access(user, 'staff', course, course.id))
