import 'package:flutter/material.dart';

void main() {
  runApp(const NovelTranslatorApp());
}

class NovelTranslatorApp extends StatelessWidget {
  const NovelTranslatorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Novel Translator',
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      home: const AppShell(),
    );
  }
}

class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int index = 0;

  final pages = const [
    HomeTaskListPage(),
    NewTaskPage(),
    TaskDetailPage(),
    ApiDebugPage(),
    RetryCenterPage(),
    SettingsPage(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Novel Translator')),
      body: pages[index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.list), label: '任务列表'),
          NavigationDestination(icon: Icon(Icons.add), label: '新建任务'),
          NavigationDestination(icon: Icon(Icons.menu_book), label: '任务详情'),
          NavigationDestination(icon: Icon(Icons.bug_report), label: 'API调试'),
          NavigationDestination(icon: Icon(Icons.refresh), label: '纠错重传'),
          NavigationDestination(icon: Icon(Icons.settings), label: '设置'),
        ],
      ),
    );
  }
}

class HomeTaskListPage extends StatelessWidget {
  const HomeTaskListPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Center(child: Text('首页 / 任务列表'));
  }
}

class NewTaskPage extends StatelessWidget {
  const NewTaskPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('新建任务', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          SizedBox(height: 8),
          Text('选择 EPUB、Provider、模型、并发参数后启动翻译任务。'),
        ],
      ),
    );
  }
}

class TaskDetailPage extends StatelessWidget {
  const TaskDetailPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.all(16),
      child: Text('任务详情（进度+章节+原文译文对照）'),
    );
  }
}

class ApiDebugPage extends StatelessWidget {
  const ApiDebugPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.all(16),
      child: Text('API 调试面板（请求/响应、耗时、状态、导出）'),
    );
  }
}

class RetryCenterPage extends StatelessWidget {
  const RetryCenterPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.all(16),
      child: Text('纠错重传中心（规则、聚类、批量重传）'),
    );
  }
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.all(16),
      child: Text('设置（Provider/模型/并发/路径）'),
    );
  }
}
