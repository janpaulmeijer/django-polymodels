
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.fields import FieldDoesNotExist
from django.db.models.sql.constants import LOOKUP_SEP

from .managers import PolymorphicManager
from .utils import copy_fields, get_content_type


EMPTY_ACCESSOR = ([], None, '')

class BasePolymorphicModel(models.Model):
    class Meta:
        abstract = True

    def type_cast(self, to=None):
        if to is None:
            content_type = getattr(self, self.content_type_field_name)
            to = content_type.model_class()
        attrs, proxy, _lookup = self._meta._subclass_accessors.get(to, EMPTY_ACCESSOR)
        # Cast to the right concrete model by going up in the 
        # SingleRelatedObjectDescriptor chain
        type_casted = self
        for attr in attrs:
            type_casted = getattr(type_casted, attr)
        # If it's a proxy model we make sure to type cast it
        if proxy:
            type_casted = copy_fields(type_casted, proxy)
        # Ensure type casting worked correctly
        if not isinstance(type_casted, to):
            raise TypeError("Failed to type cast %s to %s" % (self, to))
        return type_casted

    def save(self, *args, **kwargs):
        if self.pk is None:
            content_type = get_content_type(self.__class__, self._state.db)
            setattr(self, self.content_type_field_name, content_type)
        return super(BasePolymorphicModel, self).save(*args, **kwargs)


class PolymorphicModel(BasePolymorphicModel):
    content_type_field_name = 'content_type'
    content_type = models.ForeignKey(ContentType)

    objects = PolymorphicManager()

    class Meta:
        abstract = True


def prepare_polymorphic_model(sender, **kwargs):
    if issubclass(sender, BasePolymorphicModel):
        opts = sender._meta
        try:
            content_type_field_name = getattr(sender, 'content_type_field_name')
        except AttributeError:
            raise ImproperlyConfigured('`BasePolymorphicModel` subclasses must '
                                       'define a `content_type_field_name`.')
        else:
            try:
                content_type_field = opts.get_field(content_type_field_name)
            except FieldDoesNotExist:
                raise ImproperlyConfigured('`%s.%s.content_type_field_name` '
                                           'points to an inexistent field "%s".'
                                           % (sender.__module__,
                                              sender.__name__,
                                              content_type_field_name))
            else:
                if (not isinstance(content_type_field, models.ForeignKey) or
                    content_type_field.rel.to is not ContentType):
                    raise ImproperlyConfigured('`%s.%s.%s` must be a '
                                               '`ForeignKey` to `ContentType`.'
                                               % (sender.__module__,
                                                  sender.__name__,
                                                  content_type_field_name))
        setattr(opts, '_subclass_accessors', {})
        parents = [sender]
        proxy = sender if opts.proxy else None
        attrs = []
        while parents:
            parent = parents.pop(0)
            if issubclass(parent, BasePolymorphicModel):
                parent_opts = parent._meta
                if not parent_opts.abstract:
                    is_polymorphic_root = parent is sender
                    # We can't do `select_related` on multiple one-to-one
                    # relationships...
                    # see https://code.djangoproject.com/ticket/16572
                    lookup = LOOKUP_SEP.join(attrs[0:1])
                    parent_opts._subclass_accessors[sender] = (tuple(attrs), proxy, lookup)
                    if not parent_opts.proxy:
                        # XXX: Is there a better way to get this?
                        attrs.insert(0, parent_opts.object_name.lower())
                parents = list(parent.__bases__) + parents # mimic mro
        opts._is_polymorphic_root = is_polymorphic_root

models.signals.class_prepared.connect(prepare_polymorphic_model)