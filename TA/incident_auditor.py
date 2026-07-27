import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import pandas as pd
import re

class IncidentAuditor:
    def __init__(self, root):
        self.root = root
        self.root.title("🔍 Аудитор ИТ-инцидентов")
        self.root.geometry("1850x1080")
        
        self.incidents = []
        self.setup_gui()

    def setup_gui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)
        
        ttk.Button(top, text="📂 Загрузить файл", command=self.load_file).pack(side=tk.LEFT)
        self.file_label = ttk.Label(top, text="Файл не загружен")
        self.file_label.pack(side=tk.LEFT, padx=15)
        
        self.stats_label = ttk.Label(top, text="Статистика: —")
        self.stats_label.pack(side=tk.LEFT, padx=30)

        ttk.Label(top, text="Исполнитель:").pack(side=tk.LEFT, padx=(20,5))
        self.executor_var = tk.StringVar()
        self.executor_combo = ttk.Combobox(top, textvariable=self.executor_var, width=25, state="readonly")
        self.executor_combo.pack(side=tk.LEFT)
        self.executor_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        ttk.Label(top, text="Тип:").pack(side=tk.LEFT, padx=(15,5))
        self.type_var = tk.StringVar()
        self.type_combo = ttk.Combobox(top, textvariable=self.type_var, width=25, state="readonly")
        self.type_combo.pack(side=tk.LEFT)
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filters())

        ttk.Label(top, text="🔎 Поиск:").pack(side=tk.LEFT, padx=(20,5))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(top, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=tk.LEFT)
        self.search_var.trace("w", lambda *args: self.apply_filters())

        ttk.Button(top, text="📊 Экспорт в Excel", command=self.export_to_excel).pack(side=tk.RIGHT, padx=5)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.tabs = {}
        for name in ["Корректно", "Есть замечания", "Некорректно"]:
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=name)
            tree = ttk.Treeview(frame, columns=("ID", "Исполнитель", "Статус"), show="headings")
            tree.heading("ID", text="ID")
            tree.heading("Исполнитель", text="Исполнитель")
            tree.heading("Статус", text="Статус аудита")
            tree.column("ID", width=140)
            tree.column("Исполнитель", width=240)
            tree.column("Статус", width=420)
            tree.pack(fill=tk.BOTH, expand=True)
            tree.bind("<<TreeviewSelect>>", self.show_analysis)
            self.tabs[name] = tree

        bottom_frame = ttk.LabelFrame(self.root, text="Информация по выбранному инциденту", padding=8)
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.bottom_notebook = ttk.Notebook(bottom_frame)
        self.bottom_notebook.pack(fill=tk.BOTH, expand=True)
        
        self.analysis_text = scrolledtext.ScrolledText(self.bottom_notebook, font=("Consolas", 10))
        self.bottom_notebook.add(self.analysis_text, text="✅ Анализ по промпту")
        
        self.raw_text = scrolledtext.ScrolledText(self.bottom_notebook, font=("Consolas", 10))
        self.bottom_notebook.add(self.raw_text, text="📋 Полная информация (с подсветкой)")

        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="📋 Копировать текущую вкладку", command=self.copy_current_tab).pack(side=tk.RIGHT, padx=10)

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if not path: return
        self.file_label.config(text=path.split('/')[-1])
        self.df = pd.read_excel(path)
        
        self.incidents = []
        executors = set()
        types = set()
        
        for idx, row in self.df.iterrows():
            inc_type = str(row.get('Тип инцидента', ''))
            executor = str(row.get('Исполнитель', ''))
            self.incidents.append({
                'idx': idx,
                'ID': str(row.get('ID инцидента', '')),
                'Исполнитель': executor,
                'Тип': inc_type,
                'Описание': str(row.get('Описание', '')) + "\n" + str(row.get('Подробное описание', '')),
                'Решение': str(row.get('Решение', '')),
                'Причина': str(row.get('Причина', '')),
                'Код закрытия': str(row.get('Код закрытия', '')),
                'Корневой': str(row.get('Корневой', ''))
            })
            if executor: executors.add(executor)
            if inc_type: types.add(inc_type)
        
        self.executor_combo['values'] = [''] + sorted(list(executors))
        self.type_combo['values'] = [''] + sorted(list(types))
        
        self.refresh_all_tabs()   # ← Важно
        self.update_stats()

    def update_stats(self):
        total = len(self.incidents)
        if total == 0: return
        correct = sum(1 for inc in self.incidents if "корректно" in self.analyze_incident(inc)['Статус'].lower())
        remarks = sum(1 for inc in self.incidents if "замечания" in self.analyze_incident(inc)['Статус'].lower())
        bad = total - correct - remarks
        self.stats_label.config(text=f"Всего: {total} | Корректно: {correct} | Замечания: {remarks} | Некорректно: {bad}")

    def refresh_all_tabs(self):
        for tree in self.tabs.values():
            for i in tree.get_children(): tree.delete(i)
        
        for inc in self.incidents:
            result = self.analyze_incident(inc)
            status = result['Статус']
            tab_name = "Корректно" if "корректно" in status.lower() else \
                       "Есть замечания" if "замечания" in status.lower() else "Некорректно"
            self.tabs[tab_name].insert("", "end", values=(inc['ID'], inc['Исполнитель'][:45], status), iid=inc['idx'])

    def apply_filters(self):
        executor = self.executor_var.get()
        inc_type = self.type_var.get()
        search = self.search_var.get().strip().lower()
        
        for tree in self.tabs.values():
            for i in tree.get_children(): tree.delete(i)
        
        for inc in self.incidents:
            if (not executor or executor in inc['Исполнитель']) and \
               (not inc_type or inc_type in inc.get('Тип', '')) and \
               (not search or search in inc['ID'].lower() or search in inc['Исполнитель'].lower()):
                result = self.analyze_incident(inc)
                status = result['Статус']
                tab_name = "Корректно" if "корректно" in status.lower() else \
                           "Есть замечания" if "замечания" in status.lower() else "Некорректно"
                self.tabs[tab_name].insert("", "end", values=(inc['ID'], inc['Исполнитель'][:45], status), iid=inc['idx'])

    def analyze_incident(self, inc):
        sol = inc['Решение']
        desc = inc['Описание']
        
        what = re.search(r'Проблема[:\s]*(.+?)(?:\n|$)', desc, re.I)
        what = what.group(1).strip() if what else "Не указано явно"
        
        why = re.search(r'Причина[:\s]*(.+?)(?:\n|$)', sol, re.I)
        why = why.group(1).strip() if why else "Не указано"
        
        competencies = "Оплот / Администраторы" if re.search(r'Оплот|ЗПИ|администратор', sol, re.I) else "Не указано"
        steps = "Выполнены работы" if re.search(r'задача|хронология|выполнены', sol, re.I) else "Не детализировано"
        
        has_start = bool(re.search(r'(Время начала|Фактическое время возникновения|начало инцидента)', sol, re.I))
        has_end = bool(re.search(r'(Время устранения|Фактическое время окончания|время окончания|окончания инцидента)', sol, re.I))
        has_chronology = bool(re.search(r'хронология|краткая хронология', sol, re.I) or re.search(r'\d{2}:\d{2}', sol))
        
        gaps = []
        if not why or len(why) < 10: gaps.append("Причина")
        if not has_start: gaps.append("Время начала")
        if not has_end: gaps.append("Время окончания")
        if not has_chronology: gaps.append("Хронология")
        
        if len(gaps) == 0:
            status = "Инцидент закрыт корректно"
        elif len(gaps) == 1:
            status = "Есть замечания"
        else:
            status = "Инцидент закрыт некорректно"
        
        result = f"""Статус: {status}

Что произошло:
{what}

Почему произошло:
{why}

Привлечённые компетенции:
{competencies}

Ход устранения:
{steps}

Дата и время начала:
{'Да' if has_start else 'Нет'}

Дата и время окончания:
{'Да' if has_end else 'Нет'}

Неразрешённые вопросы:
{', '.join(gaps) if gaps else 'Нет'}

Замечания:
{', '.join(gaps) if gaps else 'Нет'}

Обоснование решения:
Анализ проведён строго по полям "Описание" и "Решение".
"""
        return {'Статус': status, 'text': result}

    def show_analysis(self, event=None):
        widget = event.widget
        sel = widget.selection()
        if not sel: return
        idx = int(sel[0])
        inc = self.incidents[idx]
        
        result = self.analyze_incident(inc)
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(tk.END, result['text'])
        
        self.show_raw_with_highlighting(inc)

    def show_raw_with_highlighting(self, inc):
        self.raw_text.delete(1.0, tk.END)
        t = self.raw_text
        t.tag_config("error", foreground="red", background="#ffe6e6")
        t.tag_config("good", foreground="green")
        
        t.insert(tk.END, f"ID: {inc['ID']}\n")
        t.insert(tk.END, f"Исполнитель: {inc['Исполнитель']}\n")
        t.insert(tk.END, f"Корневой: {inc['Корневой']}\n")
        t.insert(tk.END, f"Код закрытия: {inc['Код закрытия']}\n\n")
        
        t.insert(tk.END, "=== ОПИСАНИЕ ===\n")
        t.insert(tk.END, inc['Описание'] + "\n\n")
        
        t.insert(tk.END, "=== РЕШЕНИЕ ===\n")
        t.insert(tk.END, inc['Решение'] + "\n\n")
        
        t.insert(tk.END, "=== ПРИЧИНА ===\n")
        if inc['Причина'] and str(inc['Причина']).strip() not in ['nan', '', '—']:
            t.insert(tk.END, inc['Причина'] + "\n", "good")
        else:
            t.insert(tk.END, "ПРИЧИНА ОТСУТСТВУЕТ\n", "error")
        
        t.insert(tk.END, "\n=== ХРОНОЛОГИЯ ===\n")
        if "хронология" in inc['Решение'].lower() or re.search(r'\d{2}:\d{2}', inc['Решение']):
            t.insert(tk.END, "Хронология присутствует\n", "good")
        else:
            t.insert(tk.END, "Хронология отсутствует или слабая\n", "error")

    def copy_current_tab(self):
        current = self.bottom_notebook.select()
        index = self.bottom_notebook.index(current)
        widget = self.bottom_notebook.winfo_children()[index].winfo_children()[0]
        text = widget.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo("Копировано", "Текст скопирован")

    def export_to_excel(self):
        if not self.incidents:
            messagebox.showwarning("Ошибка", "Сначала загрузите файл")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path: return
        
        data = []
        for inc in self.incidents:
            result = self.analyze_incident(inc)
            data.append({
                'ID': inc['ID'],
                'Исполнитель': inc['Исполнитель'],
                'Статус аудита': result['Статус'],
            })
        pd.DataFrame(data).to_excel(path, index=False)
        messagebox.showinfo("Успешно", f"Файл сохранён:\n{path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = IncidentAuditor(root)
    root.mainloop()