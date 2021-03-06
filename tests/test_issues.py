# -*- coding: utf-8 -*-
import re

import django
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.http import QueryDict
from django.test import override_settings
from django.test.client import RequestFactory
from django.test.testcases import SimpleTestCase
from django.utils.encoding import force_text

import pytest
from conftest import skipIfDjangoVersion
from demo.admin import ActionsModelAdmin, admin_register
from demo.base import AdminTestCase
from demo.models import (
    ListEditableConcurrentModel, ReversionConcurrentModel, SimpleConcurrentModel
)
from demo.util import attributes, unique_id

from concurrency.admin import ConcurrentModelAdmin
from concurrency.config import CONCURRENCY_LIST_EDITABLE_POLICY_SILENT
from concurrency.exceptions import RecordModifiedError
from concurrency.forms import ConcurrentForm
from concurrency.templatetags.concurrency import identity
from concurrency.utils import refetch


def get_fake_request(params):
    u, __ = User.objects.get_or_create(username='sax')
    setattr(u, 'is_authenticated()', True)
    setattr(u, 'selected_office', False)

    request = RequestFactory().request()
    request.user = u

    querydict = QueryDict(params)
    request.POST = querydict

    return request


class TestIssue16(AdminTestCase):
    def test_concurrency(self):
        id = 1
        admin_register(ListEditableConcurrentModel, ActionsModelAdmin)
        model_admin = site._registry[ListEditableConcurrentModel]
        with attributes((ConcurrentModelAdmin, 'list_editable_policy', CONCURRENCY_LIST_EDITABLE_POLICY_SILENT),
                        (ConcurrentModelAdmin, 'form', ConcurrentForm), ):
            obj, __ = ListEditableConcurrentModel.objects.get_or_create(pk=id)
            request1 = get_fake_request('pk=%s&_concurrency_version_1=2' % id)

            model_admin.save_model(request1, obj, None, True)

            self.assertIn(obj.pk, model_admin._get_conflicts(request1))

            obj = refetch(obj)
            request2 = get_fake_request('pk=%s&_concurrency_version_1=%s' % (id, obj.version))
            model_admin.save_model(request2, obj, None, True)
            self.assertNotIn(obj.pk, model_admin._get_conflicts(request2))


class TestIssue18(SimpleTestCase):
    def test_identity_tag(self):
        id = next(unique_id)

        obj = ListEditableConcurrentModel(pk=id)
        self.assertTrue(re.match(r"^%s,\d+$" % id, identity(obj)))

        g = User(username='UserTest', pk=3)
        self.assertEqual(identity(g), force_text(g.pk))


@pytest.mark.skipif(django.VERSION[:2] >= (1, 9), reason="Skip django>=1.9")
@pytest.mark.django_db()
def test_issue_53(admin_client):
    pytest.importorskip("reversion")
    import reversion as revisions

    with revisions.create_revision():
        instance = ReversionConcurrentModel.objects.create()
    pk = instance.pk

    with revisions.create_revision():
        instance.delete()

    version_list = revisions.get_deleted(ReversionConcurrentModel)
    deleted_pk = version_list[0].pk
    admin_client.post('/admin/demo/reversionconcurrentmodel/recover/{}/'.format(deleted_pk),
                      {'username': 'aaaa'})
    assert ReversionConcurrentModel.objects.filter(id=pk).exists()


@pytest.mark.django_db()
def test_issue_54():
    m = SimpleConcurrentModel(version=0)
    m.save()
    SimpleConcurrentModel.objects.update(version=0)
    m1 = SimpleConcurrentModel.objects.get(pk=m.pk)
    m2 = SimpleConcurrentModel.objects.get(pk=m.pk)
    assert m1.version == m2.version == 0
    m1.save()
    m2.save()

    with override_settings(CONCURRENCY_VERSION_FIELD_REQUIRED=True):
        m = SimpleConcurrentModel(version=0)
        m.save()
        SimpleConcurrentModel.objects.update(version=0)
        m1 = SimpleConcurrentModel.objects.get(pk=m.pk)
        m2 = SimpleConcurrentModel.objects.get(pk=m.pk)
        assert m1.version == m2.version == 0
        m1.save()

        with pytest.raises(RecordModifiedError):
            m2.save()


@skipIfDjangoVersion("!=(1,11)")
@pytest.mark.django_db()
def test_issue_81a(monkeypatch):
    monkeypatch.setattr('demo.admin.ActionsModelAdmin.fields', ('id',))
    with pytest.raises(SystemCheckError) as e:
        call_command('check')
    assert 'concurrency.A001' in str(e.value)


@skipIfDjangoVersion("<(1,11)")
@pytest.mark.django_db()
def test_issue_81b(monkeypatch):
    fieldsets = (
        ('Standard info', {
            'fields': ('id',)
        }),
    )
    monkeypatch.setattr('demo.admin.ActionsModelAdmin.fieldsets', fieldsets)
    with pytest.raises(SystemCheckError) as e:
        call_command('check')
    assert 'concurrency.A002' in str(e.value)
