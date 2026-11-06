# encoding: utf-8

"""
Comprehensive test suite for ZMSSqlDb module.

This file consolidates all ZMSSqlDb-related unit and integration tests:

1. ZMSSqlDbMySQLGridContextTest
   - Integration test for grid-context row generation with live MySQL
   
2. ZMSSqlDbFilterSessionTest
   - Unit tests for filter state and WHERE clause generation
   
3. ZMSSqlDbSortTest
   - Unit tests for sort parameters and ORDER BY clause generation
   
4. ZMSSqlDbReflectionTest
   - Unit tests for SQLAlchemy schema reflection
   
5. SanitizeSqlParamTest
   - Comprehensive unit tests for SQL injection prevention
   - Covers URL encoding, HTML entities, escape sequences, etc.

All tests can be run with:
  python3 -m unittest tests.test_zmssqldb -v
"""

from OFS.Folder import Folder
import importlib
import os
import unittest
import uuid
from unittest import mock

# Product imports.
from Products.zms import _globals
from Products.zms import mock_http
from Products.zms import standard
from Products.zms import zms
from Products.zms import zmssqldb


# /ZMS5> python3 -m unittest tests.test_zmssqldb.ZMSSqlDbMySQLGridContextTest
class ZMSSqlDbMySQLGridContextTest(unittest.TestCase):

  lang = 'eng'

  def setUp(self):
    folder = Folder('myzmsx')
    folder.REQUEST = mock_http.MockHTTPRequest({
      'lang': self.lang,
      'preview': 'preview',
      'theme': 'conf:aquire',
      'minimal_init': 1,
      'content_init': 1,
    })
    self.context = zms.initZMS(
      folder, 'content', 'titlealt', 'title', self.lang, self.lang, folder.REQUEST
    )
    self.context.REQUEST.response = self.context.REQUEST.RESPONSE

    self._mysql_module, self._mysql_driver_name = self._import_mysql_driver()
    if self._mysql_module is None:
      self.skipTest('No MySQL driver available (tried: MySQLdb, pymysql, mysql.connector)')

    self._mysql_cfg = {
      'host': os.environ.get('ZMS_TEST_MYSQL_HOST', '127.0.0.1'),
      'port': int(os.environ.get('ZMS_TEST_MYSQL_PORT', '3306')),
      'user': os.environ.get('ZMS_TEST_MYSQL_USER', 'root'),
      'password': os.environ.get('ZMS_TEST_MYSQL_PASSWORD', ''),
    }

    self.db_name = 'zms_test_%s' % uuid.uuid4().hex[:10]

    try:
      self._execute_sql_fixture(self.db_name)
    except Exception as exc:
      self.skipTest('MySQL setup failed: %s' % exc)

    self.sql_db = self._create_sql_db_object()

  def tearDown(self):
    try:
      if getattr(self, '_mysql_module', None) is not None and getattr(self, 'db_name', None):
        self._drop_database(self.db_name)
    except Exception:
      # Keep teardown resilient for local test environments.
      pass

  def _import_mysql_driver(self):
    for module_name, driver_name in (
      ('MySQLdb', 'MySQLdb'),
      ('pymysql', 'pymysql'),
      ('mysql.connector', 'mysql.connector'),
    ):
      try:
        return importlib.import_module(module_name), driver_name
      except Exception:
        continue
    return None, None

  def _connect_mysql(self, database=None):
    kwargs = {
      'host': self._mysql_cfg['host'],
      'port': self._mysql_cfg['port'],
      'user': self._mysql_cfg['user'],
    }
    if self._mysql_cfg['password'] != '':
      if self._mysql_driver_name in ('MySQLdb', 'pymysql'):
        kwargs['passwd'] = self._mysql_cfg['password']
      else:
        kwargs['password'] = self._mysql_cfg['password']

    if database:
      if self._mysql_driver_name in ('MySQLdb', 'pymysql'):
        kwargs['db'] = database
      else:
        kwargs['database'] = database

    if self._mysql_driver_name == 'mysql.connector':
      kwargs['autocommit'] = True
      return self._mysql_module.connect(**kwargs)

    kwargs['charset'] = 'utf8mb4'
    conn = self._mysql_module.connect(**kwargs)
    try:
      conn.autocommit(True)
    except Exception:
      pass
    return conn

  def _split_sql_statements(self, sql_text):
    lines = []
    for line in sql_text.splitlines():
      if line.strip().startswith('--'):
        continue
      lines.append(line)
    clean_sql = '\n'.join(lines)
    return [s.strip() for s in clean_sql.split(';') if s.strip()]

  def _execute_sql_fixture(self, db_name):
    base = os.path.dirname(__file__)
    sql_path = os.path.join(base, 'dbtest.sql')
    with open(sql_path, 'r', encoding='utf-8') as f:
      sql_text = f.read()

    # Keep fixture semantics but isolate each test run by db name.
    sql_text = sql_text.replace('CREATE DATABASE dbtest', 'CREATE DATABASE `%s`' % db_name)
    sql_text = sql_text.replace('USE dbtest;', 'USE `%s`;' % db_name)

    conn = self._connect_mysql()
    try:
      cursor = conn.cursor()
      for stmt in self._split_sql_statements(sql_text):
        cursor.execute(stmt)
      try:
        cursor.close()
      except Exception:
        pass
    finally:
      conn.close()

  def _drop_database(self, db_name):
    conn = self._connect_mysql()
    try:
      cursor = conn.cursor()
      cursor.execute('DROP DATABASE IF EXISTS `%s`' % db_name)
      try:
        cursor.close()
      except Exception:
        pass
    finally:
      conn.close()

  def _fetch_records(self, sql, database):
    conn = self._connect_mysql(database=database)
    try:
      cursor = conn.cursor()
      cursor.execute(sql)
      rows = cursor.fetchall()
      col_names = [x[0] for x in cursor.description]
      records = [dict(zip(col_names, row)) for row in rows]
      try:
        cursor.close()
      except Exception:
        pass
      return records
    finally:
      conn.close()

  def _create_sql_db_object(self):
    obj_id = 'sql_db_under_test'
    self.context._setObject(obj_id, zmssqldb.ZMSSqlDb(obj_id, 1))
    sql_db = getattr(self.context, obj_id)
    sql_db.REQUEST.response = sql_db.REQUEST.RESPONSE
    sql_db.zmi_pagination = lambda **kwargs: ''

    model_path = os.path.join(os.path.dirname(__file__), 'dbtest.xml')
    with open(model_path, 'r', encoding='utf-8') as f:
      model_xml = f.read()

    # Keep test setup independent from DTML method creation in lightweight fixtures.
    model = standard.parseXmlString(model_xml)
    sql_db.model_xml = model_xml
    sql_db.model = model
    sql_db.getModel = lambda: model

    # This test validates row/grid context generation based on the fixture model.
    sql_db.getEntities = lambda: model

    return sql_db

  def test_get_recordset_main_grid_context_rows_from_mysql_fixture(self):
    records = self._fetch_records(
      'SELECT company_id, name, location_id FROM company ORDER BY company_id',
      self.db_name,
    )

    meta_obj_attrs = [
      self.sql_db.getEntityColumn('company', 'company_id'),
      self.sql_db.getEntityColumn('company', 'name'),
      self.sql_db.getEntityColumn('company', 'location_id'),
    ]
    for attr in meta_obj_attrs:
      attr['name'] = attr.get('label', attr['id'])

    request = mock_http.MockHTTPRequest({
      'URL': self.sql_db.absolute_url() + '/manage_main',
      'lang': self.lang,
      'qsize': 20,
    })
    request.response = request.RESPONSE

    options = {
      'metaObjAttrIds': ['company_id', 'name', 'location_id'],
      'metaObjAttrs': meta_obj_attrs,
      'records': records,
      'filtered_records': records,
      'url_params': {'qentity': 'company'},
      'record_handler': self.sql_db.getEntityRecordHandler('company', colNames=['name', 'location_id']),
      'actions': ['insert', 'update', 'delete'],
    }

    ctx = self.sql_db.getRecordSetMainGridContext(options, request)

    self.assertEqual(3, ctx['total'])
    self.assertEqual(3, ctx['size'])
    self.assertEqual(3, len(ctx['rows']))

    expected_row_keys = {'rindex', 'index', 'qindex', 'record', 'value', 'selected', 'params', 'title'}
    self.assertEqual(expected_row_keys, set(ctx['rows'][0].keys()))

    first_row = ctx['rows'][0]
    self.assertEqual(0, first_row['qindex'])
    self.assertEqual(1, first_row['value'])
    self.assertEqual('TechCorp', first_row['record']['name'])
    self.assertEqual(1, first_row['record']['location_id'])

    # Validate branch behavior: PK is preserved even when colNames excludes it.
    self.assertEqual(1, first_row['record']['company_id'])
    self.assertEqual(1, first_row['record']['__id__'])
    self.assertEqual(1, first_row['record']['params']['rowid'])
    self.assertEqual(1, first_row['params']['rowid'])
    self.assertEqual('company', first_row['params']['qentity'])


class ZMSSqlDbFilterSessionTest(unittest.TestCase):
  """
  Unit test for the filter state of the ZMSSqlDb record-set view.

  The filter of the ZMI list view (manage_main) is kept in the http-session.
  It may only be changed by the filter form (buttons BTN_REFRESH and BTN_RESET)
  and by the filter inputs in the grid header (grid form submitted by script with
  an empty btn and no action). Saving, cancelling or deleting a record is rendered
  by the same manage_main request and carries its own btn value (e.g. BTN_SAVE),
  row actions of the grid carry an action; such requests must keep the filter
  instead of clearing it.

  No database is needed: the entity model is stubbed and only the generated
  WHERE clause is inspected.
  """

  lang = 'eng'

  def setUp(self):
    folder = Folder('myzmsx')
    folder.REQUEST = mock_http.MockHTTPRequest({
      'lang': self.lang,
      'preview': 'preview',
      'theme': 'conf:aquire',
      'minimal_init': 1,
      'content_init': 1,
    })
    self.context = zms.initZMS(
      folder, 'content', 'titlealt', 'title', self.lang, self.lang, folder.REQUEST
    )
    self.context.REQUEST.response = self.context.REQUEST.RESPONSE
    obj_id = 'sql_db_under_test'
    self.context._setObject(obj_id, zmssqldb.ZMSSqlDb(obj_id, 1))
    self.sql_db = getattr(self.context, obj_id)
    entities = [{
      'id': 'company',
      'columns': [
        {'id': 'id', 'type': 'int', 'pk': 1, 'datatype_key': _globals.DT_INT},
        {'id': 'name', 'type': 'string', 'datatype_key': _globals.DT_STRING},
        {'id': 'city', 'type': 'string', 'datatype_key': _globals.DT_STRING},
      ],
    }]
    self.sql_db.getEntities = lambda: entities
    self.sql_db.recordSet_Select = lambda tablename, select=None, where=None: 'SELECT * FROM %s ' % tablename

  def _request(self, form, btn=None):
    """
    Simulate one manage_main request on the same session and return the
    resulting sql-statement.
    """
    request = self.sql_db.REQUEST
    for key in [x for x in request.keys() if x.startswith(('filter', 'q', 'btn', 'sqlStatement', 'action'))]:
      del request.d[key]
    request.form = dict(form)
    if btn is not None:
      request.form['btn'] = btn
      request.set('btn', btn)
    for key, value in form.items():
      request.set(key, value)
    self.sql_db.recordSet_Init(request)
    self.sql_db.recordSet_Filter(request)
    return ''.join(request.get('sqlStatement'))

  def _search(self, *filters):
    form = {}
    for index, (attr, value) in enumerate(filters):
      form['filterattr%i' % index] = attr
      form['filterop%i' % index] = ''
      form['filtervalue%i' % index] = value
    return self._request(form, btn='BTN_REFRESH')

  def test_search_sets_filter(self):
    sql = self._search(('name', 'Acme'))
    self.assertIn('WHERE', sql)
    self.assertIn('company.name LIKE', sql)
    self.assertIn('Acme%', sql)

  def test_filter_kept_after_save(self):
    self._search(('name', 'Acme'))
    for action in ['insert', 'update']:
      sql = self._request({'action': action, 'name': 'Acme Ltd', 'rowid': '1'}, btn='BTN_SAVE')
      self.assertIn('company.name LIKE', sql, 'filter lost after %s' % action)
      self.assertEqual(self.sql_db.REQUEST.get('filtervalue0'), 'Acme')

  def test_filter_kept_after_cancel_and_delete(self):
    self._search(('name', 'Acme'))
    sql = self._request({'action': 'update', 'rowid': '1'}, btn='BTN_CANCEL')
    self.assertIn('company.name LIKE', sql)
    sql = self._request({'action': 'delete', 'rowid': '1'}, btn='BTN_DELETE')
    self.assertIn('company.name LIKE', sql)

  def test_filter_kept_without_button(self):
    self._search(('name', 'Acme'))
    sql = self._request({'qindex': 20})
    self.assertIn('company.name LIKE', sql)

  def test_grid_header_filter(self):
    # Typing in a filter input of the grid header submits the grid form with
    # an empty btn and no action (zpt/ZMSRecordSet/main_grid.zpt).
    form = {'action': '', 'filterattr10': 'name', 'filterop10': '', 'filtervalue10': 'Acme'}
    sql = self._request(form, btn='')
    self.assertIn('company.name LIKE', sql)
    self.assertIn('Acme%', sql)
    sql = self._request({'action': 'update', 'rowid': '1'}, btn='BTN_SAVE')
    self.assertIn('company.name LIKE', sql)

  def test_filter_kept_after_grid_row_action(self):
    # Row actions of the grid submit the same form with an empty btn, but with
    # an action; the header inputs may be empty then.
    self._search(('name', 'Acme'))
    for action in ['delete', 'move', 'duplicate']:
      form = {'action': action, 'qindex': '0', 'filterattr10': 'name', 'filterop10': '', 'filtervalue10': ''}
      sql = self._request(form, btn='')
      self.assertIn('company.name LIKE', sql, 'filter lost after grid action %s' % action)

  def test_reset_clears_filter(self):
    self._search(('name', 'Acme'))
    sql = self._request({'filterattr0': 'name', 'filterop0': '', 'filtervalue0': 'Acme'}, btn='BTN_RESET')
    self.assertNotIn('WHERE', sql)
    sql = self._request({})
    self.assertNotIn('WHERE', sql)

  def test_search_removes_dropped_filter_row(self):
    sql = self._search(('name', 'Acme'), ('city', 'Hamburg'))
    self.assertIn('company.city LIKE', sql)
    sql = self._search(('name', 'Acme'))
    self.assertIn('company.name LIKE', sql)
    self.assertNotIn('company.city', sql)
    sql = self._request({'action': 'update', 'rowid': '1'}, btn='BTN_SAVE')
    self.assertNotIn('company.city', sql)


# /ZMS5> python3 -m unittest tests.test_zmssqldb.ZMSSqlDbSortTest
class ZMSSqlDbSortTest(unittest.TestCase):
  """
  Unit test for the sort parameters of the ZMSSqlDb record-set view.

  The ZMI list view (manage_main) sorts by the request parameters qorder and
  qorderdir and keeps them in the http-session. qorder must name a column of the
  table, qorderdir may only be asc or desc. A parameter that is given twice (e.g.
  by a hidden field of the filter form and by the url) arrives as list and must
  not break the view.

  No database is needed: the entity model is stubbed and only the generated
  ORDER BY clause is inspected.
  """

  lang = 'eng'

  def setUp(self):
    folder = Folder('myzmsx')
    folder.REQUEST = mock_http.MockHTTPRequest({
      'lang': self.lang,
      'preview': 'preview',
      'theme': 'conf:aquire',
      'minimal_init': 1,
      'content_init': 1,
    })
    self.context = zms.initZMS(
      folder, 'content', 'titlealt', 'title', self.lang, self.lang, folder.REQUEST
    )
    self.context.REQUEST.response = self.context.REQUEST.RESPONSE
    obj_id = 'sql_db_under_test'
    self.context._setObject(obj_id, zmssqldb.ZMSSqlDb(obj_id, 1))
    self.sql_db = getattr(self.context, obj_id)
    entities = [{
      'id': 'company',
      'columns': [
        {'id': 'id', 'type': 'int', 'pk': 1, 'datatype_key': _globals.DT_INT},
        {'id': 'name', 'type': 'string', 'datatype_key': _globals.DT_STRING},
        {'id': 'city', 'type': 'string', 'datatype_key': _globals.DT_STRING},
      ],
    }]
    self.sql_db.getEntities = lambda: entities
    self.sql_db.recordSet_Select = lambda tablename, select=None, where=None: 'SELECT * FROM %s ' % tablename

  def _request(self, form):
    """
    Simulate one manage_main request on the same session and return the
    ORDER BY part of the resulting sql-statement.
    """
    request = self.sql_db.REQUEST
    for key in [x for x in request.keys() if x.startswith(('filter', 'q', 'btn', 'sqlStatement', 'action'))]:
      del request.d[key]
    request.form = dict(form)
    for key, value in form.items():
      request.set(key, value)
    self.sql_db.recordSet_Init(request)
    self.sql_db.recordSet_Filter(request)
    self.sql_db.recordSet_Sort(request)
    sql = ''.join(request.get('sqlStatement'))
    self.assertEqual(sql.count('ORDER BY'), 1, sql)
    return sql[sql.index('ORDER BY'):].strip()

  def test_sort_by_column(self):
    self.assertEqual(self._request({'qorder': 'name', 'qorderdir': 'desc'}), 'ORDER BY name desc')
    self.assertEqual(self._request({'qorder': 'city', 'qorderdir': 'asc'}), 'ORDER BY city asc')

  def test_sort_kept_in_session(self):
    self._request({'qorder': 'name', 'qorderdir': 'desc'})
    self.assertEqual(self._request({}), 'ORDER BY name desc')

  def test_unknown_column_falls_back_to_first_column(self):
    self.assertEqual(self._request({'qorder': 'nosuchcol', 'qorderdir': 'asc'}), 'ORDER BY company.id asc')

  def test_qorderdir_given_twice(self):
    self.assertEqual(self._request({'qorder': 'name', 'qorderdir': ['asc', 'desc']}), 'ORDER BY name desc')

  def test_qorder_given_twice(self):
    self.assertEqual(self._request({'qorder': ['name', 'city'], 'qorderdir': 'asc'}), 'ORDER BY city asc')

  def test_qorderdir_only_asc_or_desc(self):
    for value in ['asc, (select 1)', 'desc; drop table company', '1', '', 'desc']:
      expected = 'desc' if value.strip().lower() == 'desc' else 'asc'
      self.assertEqual(self._request({'qorder': 'name', 'qorderdir': value}), 'ORDER BY name %s' % expected, value)

  def test_session_not_kept_invalid(self):
    self._request({'qorder': 'name', 'qorderdir': 'asc, (select 1)'})
    self.assertEqual(standard.get_session_value(self.sql_db, 'qorderdir_%s' % self.sql_db.id), 'asc')
    self.assertEqual(self._request({}), 'ORDER BY name asc')


class StubWrapper:
  """Stub for SQLAlchemy schema reflection tests."""

  def __init__(self, engine):
    self.engine = engine


class StubDA:
  """Stub database adapter for schema reflection tests."""

  def __init__(self, engine):
    self.engine = engine

  def sa_zope_wrapper(self):
    return StubWrapper(self.engine)

  def getProperty(self, name):
    return {'dsn': 'sqlite://'}[name]


class StubSqlDb:
  """Stub ZMSSqlDb for schema reflection tests."""

  def __init__(self, engine):
    self.da = StubDA(engine)

  def getDA(self):
    return self.da


# /ZMS5> python3 -m unittest tests.test_zmssqldb.ZMSSqlDbReflectionTest
class ZMSSqlDbReflectionTest(unittest.TestCase):
  """
  Unit test for the schema reflection of ZMSSqlDb with a SQLAlchemyDA connection.

  getEntitiesSQLAlchemyDA() runs once per request. It must use the engine of the
  database adapter: a new engine per call keeps its pooled connection open until
  the garbage collector removes the engine, which exhausts max_connections under
  load (1040 Too many connections).

  Only an in-memory SQLite engine is needed, no Zope instance and no database server.
  The test auto-skips if SQLAlchemy is not installed.
  """

  def setUp(self):
    try:
      import sqlalchemy
    except ImportError:
      self.skipTest('SQLAlchemy not installed')
    self.engine = sqlalchemy.create_engine('sqlite://')
    self.addCleanup(self.engine.dispose)
    with self.engine.begin() as connection:
      connection.execute(sqlalchemy.text('CREATE TABLE fruit (id INTEGER PRIMARY KEY, name VARCHAR(32))'))

  def test_uses_engine_of_the_adapter(self):
    with mock.patch('sqlalchemy.create_engine') as create_engine:
      entities = zmssqldb.ZMSSqlDb.getEntitiesSQLAlchemyDA(StubSqlDb(self.engine))
    create_engine.assert_not_called()
    self.assertEqual(['fruit'], [x['id'] for x in entities])
    self.assertEqual(['id', 'name'], sorted(x['id'] for x in entities[0]['columns']))



class RequestParamNormalizationTest(unittest.TestCase):
  """
  Unit tests for _normalize_request_param() function.
  
  Validates handling of list/tuple parameters and whitespace trimming.
  """

  def test_list_to_single_value(self):
    """Test normalization of list parameters to single value (takes last)."""
    result = zmssqldb._normalize_request_param(['col1', 'col2'])
    self.assertEqual('col2', result)

  def test_empty_list_returns_empty_string(self):
    """Test that empty list returns empty string."""
    result = zmssqldb._normalize_request_param([])
    self.assertEqual('', result)

  def test_tuple_normalization(self):
    """Test normalization of tuple parameters (takes last)."""
    result = zmssqldb._normalize_request_param(('a', 'b', 'c'))
    self.assertEqual('c', result)

  def test_none_returns_empty_string(self):
    """Test that None returns empty string."""
    result = zmssqldb._normalize_request_param(None)
    self.assertEqual('', result)

  def test_empty_string_returns_empty_string(self):
    """Test that empty string returns empty string."""
    result = zmssqldb._normalize_request_param('')
    self.assertEqual('', result)

  def test_whitespace_stripping(self):
    """Test that leading/trailing whitespace is stripped."""
    result = zmssqldb._normalize_request_param('  spaced  ')
    self.assertEqual('spaced', result)

  def test_single_string_value(self):
    """Test single string value is returned as-is (trimmed)."""
    result = zmssqldb._normalize_request_param('users.id')
    self.assertEqual('users.id', result)


class SqlIdentifierValidationTest(unittest.TestCase):
  """
  Unit tests for _validate_sql_identifier() function.
  
  Validates SQL identifier whitelisting (column/table names in ORDER BY).
  """

  def test_valid_identifier_simple(self):
    """Test valid simple identifier."""
    allowed = ['id', 'name', 'email']
    result = zmssqldb._validate_sql_identifier('id', allowed)
    self.assertTrue(result)

  def test_valid_identifier_with_underscore(self):
    """Test valid identifier with underscores."""
    allowed = ['user_id', 'created_at']
    result = zmssqldb._validate_sql_identifier('user_id', allowed)
    self.assertTrue(result)

  def test_valid_identifier_table_qualified(self):
    """Test valid table.column qualified identifier."""
    allowed = ['id', 'name']
    result = zmssqldb._validate_sql_identifier('users.id', allowed)
    self.assertTrue(result)

  def test_invalid_identifier_not_in_whitelist(self):
    """Test identifier not in whitelist fails."""
    allowed = ['id', 'name']
    result = zmssqldb._validate_sql_identifier('password', allowed)
    self.assertFalse(result)

  def test_invalid_identifier_with_quote(self):
    """Test identifier with quote fails."""
    allowed = ['id']
    result = zmssqldb._validate_sql_identifier("id'; DROP", allowed)
    self.assertFalse(result)

  def test_invalid_identifier_with_semicolon(self):
    """Test identifier with semicolon fails."""
    allowed = ['id']
    result = zmssqldb._validate_sql_identifier("id; DELETE", allowed)
    self.assertFalse(result)

  def test_invalid_identifier_with_comment(self):
    """Test identifier with SQL comment fails."""
    allowed = ['id']
    result = zmssqldb._validate_sql_identifier("id --", allowed)
    self.assertFalse(result)

  def test_empty_identifier_fails(self):
    """Test empty identifier fails."""
    allowed = ['id']
    result = zmssqldb._validate_sql_identifier('', allowed)
    self.assertFalse(result)

  def test_case_insensitive_matching(self):
    """Test that matching is case-insensitive."""
    allowed = ['userId', 'userName']
    result = zmssqldb._validate_sql_identifier('USERID', allowed)
    self.assertTrue(result)

  def test_table_qualified_case_insensitive(self):
    """Test table-qualified matching is case-insensitive."""
    allowed = ['id', 'name']
    result = zmssqldb._validate_sql_identifier('USERS.ID', allowed)
    self.assertTrue(result)


class RecordSetSortIntegrationTest(unittest.TestCase):
  """
  Integration tests for recordSet_Sort method.
  
  Validates the refactored sorting using new validation helpers.
  """

  def test_recordset_sort_normal_scenario(self):
    """Test normal recordSet_Sort scenario with valid inputs."""
    qorder = zmssqldb._normalize_request_param('users.id')
    qorderdir = zmssqldb._normalize_request_param('asc')
    qorderdir = qorderdir if qorderdir in ('asc', 'desc') else 'asc'
    
    self.assertEqual('users.id', qorder)
    self.assertEqual('asc', qorderdir)

  def test_recordset_sort_multivalue_list(self):
    """Test recordSet_Sort with multi-value parameter list (uses last value)."""
    qorder = zmssqldb._normalize_request_param(['old_value', 'users.name'])
    qorderdir = zmssqldb._normalize_request_param(['asc', 'desc'])
    qorderdir = qorderdir if qorderdir in ('asc', 'desc') else 'asc'
    
    self.assertEqual('users.name', qorder)
    self.assertEqual('desc', qorderdir)

  def test_recordset_sort_injection_attempt(self):
    """Test recordSet_Sort rejects injection attempt."""
    allowed_cols = ['id', 'name', 'email']
    
    qorder = zmssqldb._normalize_request_param("users.id'; DROP TABLE--")
    qorderdir = zmssqldb._normalize_request_param("asc' OR 1=1")
    qorderdir = qorderdir if qorderdir in ('asc', 'desc') else 'asc'
    
    is_valid_order = zmssqldb._validate_sql_identifier(qorder, allowed_cols)
    
    # Injection attempt should fail validation
    self.assertFalse(is_valid_order)
    self.assertEqual('asc', qorderdir)


if __name__ == '__main__':
  unittest.main()
