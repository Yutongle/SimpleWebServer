# 员工信息管理系统 - 完整可运行代码
import tkinter as tk
from tkinter import ttk, messagebox
import json
import os


class EmployeeSystem:
    def __init__(self, root):
        self.root = root
        self.root.title("员工信息管理系统")
        self.root.geometry("800x550")

        # 员工数据列表 + 数据文件
        self.emp_list = []
        self.file_path = "emp_data.json"
        self.load_data()  # 加载本地数据

        # 创建界面
        self.create_ui()

    # 从文件加载数据
    def load_data(self):
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                self.emp_list = json.load(f)

    # 保存数据到文件
    def save_data(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.emp_list, f, ensure_ascii=False, indent=2)

    # 创建界面组件
    def create_ui(self):
        # 标题
        tk.Label(self.root, text="员工信息管理系统", font=("微软雅黑", 18, "bold")).pack(pady=10)

        # ========== 输入区域 ==========
        frame_input = tk.Frame(self.root)
        frame_input.pack(pady=5)

        # 标签与输入框
        tk.Label(frame_input, text="员工ID：").grid(row=0, column=0, padx=5, pady=5)
        self.entry_id = tk.Entry(frame_input)
        self.entry_id.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(frame_input, text="姓名：").grid(row=0, column=2, padx=5, pady=5)
        self.entry_name = tk.Entry(frame_input)
        self.entry_name.grid(row=0, column=3, padx=5, pady=5)

        tk.Label(frame_input, text="年龄：").grid(row=1, column=0, padx=5, pady=5)
        self.entry_age = tk.Entry(frame_input)
        self.entry_age.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(frame_input, text="部门：").grid(row=1, column=2, padx=5, pady=5)
        self.entry_dept = tk.Entry(frame_input)
        self.entry_dept.grid(row=1, column=3, padx=5, pady=5)

        tk.Label(frame_input, text="职位：").grid(row=2, column=0, padx=5, pady=5)
        self.entry_job = tk.Entry(frame_input)
        self.entry_job.grid(row=2, column=1, padx=5, pady=5)

        tk.Label(frame_input, text="薪资：").grid(row=2, column=2, padx=5, pady=5)
        self.entry_sal = tk.Entry(frame_input)
        self.entry_sal.grid(row=2, column=3, padx=5, pady=5)

        # ========== 按钮区域 ==========
        frame_btn = tk.Frame(self.root)
        frame_btn.pack(pady=5)

        tk.Button(frame_btn, text="添加", width=8, command=self.add_emp).grid(row=0, column=0, padx=3)
        tk.Button(frame_btn, text="删除", width=8, command=self.del_emp).grid(row=0, column=1, padx=3)
        tk.Button(frame_btn, text="修改", width=8, command=self.update_emp).grid(row=0, column=2, padx=3)
        tk.Button(frame_btn, text="查询", width=8, command=self.query_emp).grid(row=0, column=3, padx=3)
        tk.Button(frame_btn, text="清空", width=8, command=self.clear_input).grid(row=0, column=4, padx=3)
        tk.Button(frame_btn, text="退出", width=8, command=self.root.quit).grid(row=0, column=5, padx=3)

        # ========== 表格区域 ==========
        frame_table = tk.Frame(self.root)
        frame_table.pack(pady=10, fill=tk.BOTH, expand=True)

        # 表格列
        columns = ("id", "name", "age", "dept", "job", "sal")
        self.tree = ttk.Treeview(frame_table, columns=columns, show="headings")

        # 表头
        self.tree.heading("id", text="员工ID")
        self.tree.heading("name", text="姓名")
        self.tree.heading("age", text="年龄")
        self.tree.heading("dept", text="部门")
        self.tree.heading("job", text="职位")
        self.tree.heading("sal", text="薪资")

        # 列宽
        self.tree.column("id", width=80)
        self.tree.column("name", width=100)
        self.tree.column("age", width=60)
        self.tree.column("dept", width=120)
        self.tree.column("job", width=120)
        self.tree.column("sal", width=100)

        # 滚动条
        scroll = ttk.Scrollbar(frame_table, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # 绑定表格选中事件
        self.tree.bind("<<TreeviewSelect>>", self.select_item)

        # 刷新表格
        self.refresh_table()

    # 刷新表格显示
    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for emp in self.emp_list:
            self.tree.insert("", tk.END, values=(
                emp["id"], emp["name"], emp["age"], emp["dept"], emp["job"], emp["sal"]
            ))

    # 表格选中回填输入框
    def select_item(self, event):
        selected = self.tree.selection()
        if selected:
            data = self.tree.item(selected[0])["values"]
            self.clear_input()
            self.entry_id.insert(0, data[0])
            self.entry_name.insert(0, data[1])
            self.entry_age.insert(0, data[2])
            self.entry_dept.insert(0, data[3])
            self.entry_job.insert(0, data[4])
            self.entry_sal.insert(0, data[5])

    # 获取输入框内容
    def get_input(self):
        return {
            "id": self.entry_id.get().strip(),
            "name": self.entry_name.get().strip(),
            "age": self.entry_age.get().strip(),
            "dept": self.entry_dept.get().strip(),
            "job": self.entry_job.get().strip(),
            "sal": self.entry_sal.get().strip()
        }

    # 清空输入框
    def clear_input(self):
        self.entry_id.delete(0, tk.END)
        self.entry_name.delete(0, tk.END)
        self.entry_age.delete(0, tk.END)
        self.entry_dept.delete(0, tk.END)
        self.entry_job.delete(0, tk.END)
        self.entry_sal.delete(0, tk.END)

    # ========== 核心功能 ==========
    # 添加员工
    def add_emp(self):
        emp = self.get_input()
        if not emp["id"] or not emp["name"]:
            messagebox.showerror("错误", "ID和姓名不能为空！")
            return
        # 检查ID重复
        for e in self.emp_list:
            if e["id"] == emp["id"]:
                messagebox.showerror("错误", "该ID已存在！")
                return
        self.emp_list.append(emp)
        self.save_data()
        self.refresh_table()
        self.clear_input()
        messagebox.showinfo("成功", "添加完成！")

    # 删除员工
    def del_emp(self):
        emp = self.get_input()
        if not emp["id"]:
            messagebox.showerror("错误", "请选择要删除的员工！")
            return
        for i in range(len(self.emp_list)):
            if self.emp_list[i]["id"] == emp["id"]:
                del self.emp_list[i]
                self.save_data()
                self.refresh_table()
                self.clear_input()
                messagebox.showinfo("成功", "删除完成！")
                return
        messagebox.showerror("错误", "未找到该员工！")

    # 修改员工
    def update_emp(self):
        emp = self.get_input()
        if not emp["id"]:
            messagebox.showerror("错误", "请选择要修改的员工！")
            return
        for i in range(len(self.emp_list)):
            if self.emp_list[i]["id"] == emp["id"]:
                self.emp_list[i] = emp
                self.save_data()
                self.refresh_table()
                self.clear_input()
                messagebox.showinfo("成功", "修改完成！")
                return
        messagebox.showerror("错误", "未找到该员工！")

    # 查询员工
    def query_emp(self):
        emp_id = self.entry_id.get().strip()
        if not emp_id:
            messagebox.showerror("错误", "请输入要查询的ID！")
            return
        for e in self.emp_list:
            if e["id"] == emp_id:
                self.clear_input()
                self.entry_id.insert(0, e["id"])
                self.entry_name.insert(0, e["name"])
                self.entry_age.insert(0, e["age"])
                self.entry_dept.insert(0, e["dept"])
                self.entry_job.insert(0, e["job"])
                self.entry_sal.insert(0, e["sal"])
                messagebox.showinfo("成功", "查询到该员工！")
                return
        messagebox.showinfo("提示", "未查询到该员工！")


# 主程序入口
if __name__ == "__main__":
    root = tk.Tk()
    app = EmployeeSystem(root)
    root.mainloop()