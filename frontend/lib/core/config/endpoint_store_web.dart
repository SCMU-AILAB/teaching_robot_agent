// frontend/lib/core/config/endpoint_store_web.dart
import 'package:web/web.dart' as web;

const String _key = 'teaching_robot.device_api_base';

Future<String?> loadEndpoint() async {
  final String? value = web.window.localStorage.getItem(_key);
  return value == null || value.isEmpty ? null : value;
}

Future<void> saveEndpoint(String baseUrl) async =>
    web.window.localStorage.setItem(_key, baseUrl);

Future<void> clearEndpoint() async => web.window.localStorage.removeItem(_key);
