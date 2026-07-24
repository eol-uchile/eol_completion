# Python Standard Libraries
import logging

# Edx dependencies
from django.core.cache import cache
from django.core.management.base import BaseCommand
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Borra el cache de eol_completion para todos los cursos."
    def handle(self, *args, **options):
        course_ids = CourseOverview.objects.values_list("id", flat=True)
        keys = ["eol_completion-{course_id}-data".format(course_id=cid) for cid in course_ids]
        count = 0
        for key in keys:
            if cache.get(key):
                cache.delete(key)
                logger.info(f"El cache de {key} ha sido borrado.")
                count += 1
        logger.info(f"{count} eol_completions caches borrados.")
