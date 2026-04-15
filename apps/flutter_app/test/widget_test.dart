import 'package:flutter_test/flutter_test.dart';
import 'package:novel_translator_app/main.dart';

void main() {
  testWidgets('app shell renders', (tester) async {
    await tester.pumpWidget(const NovelTranslatorApp());
    expect(find.text('Novel Translator'), findsOneWidget);
  });
}
