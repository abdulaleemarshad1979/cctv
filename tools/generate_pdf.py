import os
import re
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted, HRFlowable, Table, TableStyle, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        
        # Color palette
        teal = colors.HexColor("#0D5C75")
        grey = colors.HexColor("#666666")
        light_grey = colors.HexColor("#EAEAEA")

        # Top running header (suppressed on page 1)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(teal)
            self.drawString(54, 750, "PUSHKARALU DRONE CROWD MONITOR & RISK ENGINE")
            self.setFont("Helvetica", 8)
            self.setFillColor(grey)
            self.drawRightString(612 - 54, 750, "SYSTEM GUIDE")
            
            # Header line
            self.setStrokeColor(light_grey)
            self.setLineWidth(0.5)
            self.line(54, 742, 612 - 54, 742)

        # Bottom running footer (on all pages)
        self.setFont("Helvetica", 9)
        self.setFillColor(grey)
        self.drawString(54, 36, "Pushkaralu Safety System — Operational Guide")
        self.drawRightString(612 - 54, 36, f"Page {self._pageNumber} of {page_count}")
        
        # Footer line
        self.setStrokeColor(light_grey)
        self.setLineWidth(0.5)
        self.line(54, 48, 612 - 54, 48)
        
        self.restoreState()

def clean_inline(text):
    """Clean inline markdown and escape HTML/XML characters safely for ReportLab."""
    # Convert bold and italic first to temporary placeholders
    text = re.sub(r"\*\*(.*?)\*\*", r"___BOLD___\1___ENDBOLD___", text)
    text = re.sub(r"\*(.*?)\*", r"___ITALIC___\1___ENDITALIC___", text)
    text = re.sub(r"`(.*?)`", r"___CODE___\1___ENDCODE___", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    
    # Math replacement cleanup for readability
    text = text.replace("$$\\text{Turbulence} = \\operatorname{Var}(\\theta_{\\text{flow}}) \\times \\|\\vec{v}\\|$$", "<b>Turbulence</b> = Var(θ_flow) × ||v||")
    text = text.replace("$$\\text{Corrected Count} = \\text{Raw Density Count} \\times \\left( \\frac{h_{\\text{current}}}{h_{\\text{baseline}}} \\right)^{\\gamma}$$", "<b>Corrected Count</b> = Raw Count × (h_current / h_baseline)^γ")
    text = text.replace("$$\\text{Risk Score} = w_d \\cdot D_{\\norm} + w_v \\cdot V_{\\norm} + w_t \\cdot T_{\\norm} + w_c \\cdot C_{\\norm}$$", "<b>Risk Score</b> = w_d · D_norm + w_v · V_norm + w_t · T_norm + w_c · C_norm")
    text = re.sub(r"\$\$(.*?)\$\$", r"<i>\1</i>", text)
    text = re.sub(r"\$(.*?)\$", r"<i>\1</i>", text)

    # Escape XML entities
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Restore placeholders to ReportLab tags
    text = text.replace("___BOLD___", "<b>").replace("___ENDBOLD___", "</b>")
    text = text.replace("___ITALIC___", "<i>").replace("___ENDITALIC___", "</i>")
    text = text.replace("___CODE___", "<font name='Courier'>").replace("___ENDCODE___", "</font>")
    
    return text

def parse_markdown_to_flowables(filepath, styles):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    flowables = []
    
    # Split content by code blocks and normal text
    parts = re.split(r"(```.*?```)", content, flags=re.DOTALL)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        if part.startswith("```"):
            # Diagram / Code block
            lines = part.strip("`").strip("\n").split("\n")
            if lines and not lines[0].startswith(" ") and len(lines[0]) < 10 and not any(c in lines[0] for c in "|+-"):
                lines = lines[1:]
            text_content = "\n".join(lines)
            
            flowables.append(Spacer(1, 6))
            flowables.append(Preformatted(text_content, styles["DiagramStyle"]))
            flowables.append(Spacer(1, 6))
        else:
            lines = part.split("\n")
            table_lines = []
            
            for line in lines:
                line_str = line.strip()
                
                # Table collector
                if line_str.startswith("|") and line_str.endswith("|"):
                    table_lines.append(line_str)
                    continue
                elif table_lines:
                    # Process accumulated table
                    flowables.append(Spacer(1, 6))
                    flowables.append(build_table(table_lines, styles))
                    flowables.append(Spacer(1, 8))
                    table_lines = []
                
                if not line_str:
                    continue

                if line_str == "---":
                    flowables.append(Spacer(1, 6))
                    flowables.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#D1D5DB"), spaceBefore=5, spaceAfter=12))
                    continue

                if line_str.startswith("# "):
                    title_text = clean_inline(line_str[2:])
                    flowables.append(Spacer(1, 15))
                    flowables.append(Paragraph(title_text, styles["DocTitle"]))
                    flowables.append(Spacer(1, 10))
                elif line_str.startswith("## "):
                    h1_text = clean_inline(line_str[3:])
                    flowables.append(Spacer(1, 12))
                    flowables.append(Paragraph(h1_text, styles["Heading1"]))
                    flowables.append(Spacer(1, 8))
                elif line_str.startswith("### "):
                    h2_text = clean_inline(line_str[4:])
                    flowables.append(Spacer(1, 10))
                    flowables.append(Paragraph(h2_text, styles["Heading2"]))
                    flowables.append(Spacer(1, 6))
                elif line_str.startswith("> "):
                    quote_text = clean_inline(line_str[2:])
                    flowables.append(Spacer(1, 4))
                    flowables.append(Paragraph(quote_text, styles["QuoteText"]))
                    flowables.append(Spacer(1, 4))
                elif line_str.startswith("* ") or line_str.startswith("- "):
                    list_text = clean_inline(line_str[2:])
                    flowables.append(Paragraph(f"<bullet>&bull;</bullet>{list_text}", styles["BulletText"]))
                elif re.match(r"^\d+\.\s", line_str):
                    num_match = re.match(r"^(\d+)\.\s+(.*)", line_str)
                    if num_match:
                        num, num_text = num_match.groups()
                        flowables.append(Paragraph(f"<b>{num}.</b> {clean_inline(num_text)}", styles["BodyText"]))
                else:
                    para_text = clean_inline(line_str)
                    flowables.append(Paragraph(para_text, styles["BodyText"]))

            if table_lines:
                flowables.append(Spacer(1, 6))
                flowables.append(build_table(table_lines, styles))
                flowables.append(Spacer(1, 8))

    return flowables

def build_table(table_lines, styles):
    rows = []
    for line in table_lines:
        # Check if delimiter line like |---|---|
        if re.match(r"^\|[\s:\|-]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return Spacer(1, 1)

    table_data = []
    for row_idx, row in enumerate(rows):
        formatted_row = []
        for cell in row:
            style = styles["TableHeader"] if row_idx == 0 else styles["TableCell"]
            formatted_row.append(Paragraph(clean_inline(cell), style))
        table_data.append(formatted_row)

    # Column widths calculation based on number of columns
    num_cols = max(len(r) for r in table_data)
    total_width = 504 # 612 - 54*2
    col_width = total_width / num_cols
    col_widths = [col_width] * num_cols

    t = Table(table_data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0D5C75")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    return t

def main():
    workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md_path = os.path.join(workspace_dir, "SYSTEM_GUIDE.md")
    pdf_path = os.path.join(workspace_dir, "SYSTEM_GUIDE.pdf")

    if not os.path.exists(md_path):
        print(f"Error: {md_path} not found.")
        sys.exit(1)

    print(f"Reading markdown from {md_path}...")

    primary_color = colors.HexColor("#0D5C75")
    text_color = colors.HexColor("#1E293B")
    bg_diagram = colors.HexColor("#F8FAFC")
    border_diagram = colors.HexColor("#E2E8F0")

    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="DocTitle",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary_color,
        spaceAfter=12,
        alignment=0
    ))

    styles["Heading1"].fontName = "Helvetica-Bold"
    styles["Heading1"].fontSize = 13
    styles["Heading1"].leading = 17
    styles["Heading1"].textColor = primary_color
    styles["Heading1"].spaceBefore = 14
    styles["Heading1"].spaceAfter = 8
    styles["Heading1"].keepWithNext = True

    styles["Heading2"].fontName = "Helvetica-Bold"
    styles["Heading2"].fontSize = 10.5
    styles["Heading2"].leading = 14
    styles["Heading2"].textColor = colors.HexColor("#334155")
    styles["Heading2"].spaceBefore = 10
    styles["Heading2"].spaceAfter = 4
    styles["Heading2"].keepWithNext = True

    styles["BodyText"].fontName = "Helvetica"
    styles["BodyText"].fontSize = 9.5
    styles["BodyText"].leading = 13.5
    styles["BodyText"].textColor = text_color
    styles["BodyText"].spaceAfter = 6

    styles.add(ParagraphStyle(
        name="BulletText",
        parent=styles["BodyText"],
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=4
    ))

    styles.add(ParagraphStyle(
        name="QuoteText",
        parent=styles["BodyText"],
        fontName="Helvetica-Oblique",
        leftIndent=15,
        rightIndent=15,
        textColor=colors.HexColor("#475569"),
        backColor=colors.HexColor("#F1F5F9"),
        borderPadding=6,
        spaceAfter=6
    ))

    styles.add(ParagraphStyle(
        name="TableHeader",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.white
    ))

    styles.add(ParagraphStyle(
        name="TableCell",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=text_color
    ))

    styles.add(ParagraphStyle(
        name="DiagramStyle",
        fontName="Courier",
        fontSize=7,
        leading=8.8,
        textColor=colors.HexColor("#0F172A"),
        backColor=bg_diagram,
        borderColor=border_diagram,
        borderWidth=0.5,
        borderPadding=8,
        spaceAfter=8
    ))

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=72,
        bottomMargin=72
    )

    flowables = parse_markdown_to_flowables(md_path, styles)
    
    print(f"Generating PDF at {pdf_path}...")
    doc.build(flowables, canvasmaker=NumberedCanvas)
    print("PDF Generation complete!")

if __name__ == "__main__":
    main()
