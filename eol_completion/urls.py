

from django.conf.urls import url
from django.conf import settings
from .views import EolCompletionFragmentView, EolCompletionData, EolCompletionFragmentFastView, EolCompletionDataFast

urlpatterns = (
    url(
        r'courses/{}/student_completion$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        EolCompletionFragmentView.as_view(),
        name='completion_view',
    ),
    url(
        r'courses/{}/student_completion/data$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        EolCompletionData.as_view(),
        name='completion_data_view',
    ),
    url(
        r'courses/{}/student_completion_fast$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        EolCompletionFragmentFastView.as_view(),
        name='completion_fast_view',
    ),
    url(
        r'courses/{}/student_completion_fast/data$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        EolCompletionDataFast.as_view(),
        name='completion_data_fast_view',
    ),
)
