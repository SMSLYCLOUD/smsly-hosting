import os
from docx import Document
from docx.shared import Inches
from openpyxl import Workbook
from fpdf import FPDF
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

OUTPUT_DIR = "test-results/skills_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def test_chart_skill():
    print("Testing Chart Skill...")
    try:
        # Generate data
        x = np.linspace(0, 10, 100)
        y = np.sin(x)

        plt.figure(figsize=(10, 6))
        plt.plot(x, y, label='Sine Wave')
        plt.title('Adversarial Test Chart')
        plt.xlabel('X Axis')
        plt.ylabel('Y Axis')
        plt.legend()
        plt.grid(True)

        output_path = os.path.join(OUTPUT_DIR, "test_chart.png")
        plt.savefig(output_path)
        plt.close()
        print(f"✅ Chart created at {output_path}")
        return output_path
    except Exception as e:
        print(f"❌ Chart creation failed: {e}")
        return None

def test_docx_skill(image_path):
    print("Testing DOCX Skill...")
    try:
        doc = Document()
        doc.add_heading('Jules QA Protocol - DOCX Test', 0)
        doc.add_paragraph('This is a test of the document generation capability.')

        # Add table
        table = doc.add_table(rows=3, cols=3)
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = 'ID'
        hdr_cells[1].text = 'Name'
        hdr_cells[2].text = 'Status'

        for i in range(1, 3):
            row_cells = table.rows[i].cells
            row_cells[0].text = str(i)
            row_cells[1].text = f'Item {i}'
            row_cells[2].text = 'Active'

        # Add image if exists
        if image_path and os.path.exists(image_path):
            doc.add_picture(image_path, width=Inches(4))

        doc_path = os.path.join(OUTPUT_DIR, "test_doc.docx")
        doc.save(doc_path)
        print(f"✅ DOCX created at {doc_path}")
    except Exception as e:
        print(f"❌ DOCX creation failed: {e}")

def test_xlsx_skill():
    print("Testing XLSX Skill...")
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "QA Data"

        # Headers
        ws['A1'] = "Value A"
        ws['B1'] = "Value B"
        ws['C1'] = "Sum"

        # Data
        for i in range(2, 102): # 100 rows
            ws[f'A{i}'] = i * 10
            ws[f'B{i}'] = i * 5
            ws[f'C{i}'] = f"=SUM(A{i}, B{i})"

        xlsx_path = os.path.join(OUTPUT_DIR, "test_sheet.xlsx")
        wb.save(xlsx_path)
        print(f"✅ XLSX created at {xlsx_path}")
    except Exception as e:
        print(f"❌ XLSX creation failed: {e}")

def test_pdf_skill():
    print("Testing PDF Skill...")
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, txt="Jules QA Protocol - PDF Test", ln=1, align="C")
        pdf.cell(200, 10, txt="Generated via FPDF library.", ln=2, align="L")

        for i in range(1, 20):
            pdf.cell(200, 10, txt=f"Line {i}: This is a stress test line.", ln=1)

        pdf_path = os.path.join(OUTPUT_DIR, "test_doc.pdf")
        pdf.output(pdf_path)
        print(f"✅ PDF created at {pdf_path}")
    except Exception as e:
        print(f"❌ PDF creation failed: {e}")

if __name__ == "__main__":
    print("🚀 Starting Skills Simulation...")
    chart_path = test_chart_skill()
    test_docx_skill(chart_path)
    test_xlsx_skill()
    test_pdf_skill()
    print("🏁 Skills Simulation Complete.")
