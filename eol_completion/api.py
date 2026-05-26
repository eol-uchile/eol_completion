# Installed packages (via pip)
from django.contrib.auth.models import User

# Edx dependencies
from common.djangoapps.student.models import CourseAccessRole

# Internal project dependencies
from  utils import get_completed_percentage_per_user

#TODO: obtain info
def get_completion_per_user(info, course_key):
    """
    
    """
    staff_query = CourseAccessRole.objects.filter(
                course_id=course_key
            ).values('user_id')
    enrolled_students = User.objects.filter(
        courseenrollment__course_id=course_key,
        courseenrollment__is_active=1
    ).exclude(
        id__in=staff_query
    ).order_by('username').values('id', 'username', 'email')
    return get_completed_percentage_per_user(info, enrolled_students, course_key)
