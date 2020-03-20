from __future__ import absolute_import

from django.conf.urls import url
from django.conf import settings
from .views import EolCompletionFragmentView, EolCompletionData


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
)
