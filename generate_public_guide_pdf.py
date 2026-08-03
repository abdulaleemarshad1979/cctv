import sys
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class PublicGuideCanvas(canvas.Canvas):
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
        
        # Header (Only if > 1 page)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#1E293B"))
            self.drawString(28, letter[1] - 20, "PUBLIC SAFETY DRONE CROWD MONITORING PLATFORM")
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawRightString(letter[0] - 28, letter[1] - 20, "SYSTEM GUIDE")
            self.setStrokeColor(colors.HexColor("#CBD5E0"))
            self.setLineWidth(0.75)
            self.line(28, letter[1] - 24, letter[0] - 28, letter[1] - 24)

        # Footer (All pages)
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(28, 18, "Public Safety Drone Crowd Monitor & Risk Engine — User & Operational Guide")
        
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 28, 18, page_text)
        
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.75)
        self.line(28, 28, letter[0] - 28, 28)

        self.restoreState()


def build_pdf(filename="SYSTEM_GUIDE_PUBLIC.pdf"):
    # Printable width: 612 - 56 = 556pt
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=32
    )

    styles = getSampleStyleSheet()
    
    COLOR_NAVY = colors.HexColor("#0F172A")       # Primary Dark Slate
    COLOR_TEAL = colors.HexColor("#0F766E")       # Accent Teal
    COLOR_TEXT = colors.HexColor("#334155")       # Body Text
    COLOR_BG_LIGHT = colors.HexColor("#F8FAFC")   # Light Card BG
    COLOR_BORDER = colors.HexColor("#E2E8F0")     # Border Slate

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=COLOR_NAVY,
        spaceAfter=1
    )

    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11.5,
        textColor=COLOR_TEAL,
        spaceAfter=0
    )

    h1_style = ParagraphStyle(
        'SectionH1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12.5,
        textColor=COLOR_NAVY,
        spaceBefore=4,
        spaceAfter=2.5
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.8,
        leading=10.5,
        textColor=COLOR_TEXT,
        spaceAfter=2
    )

    bullet_style = ParagraphStyle(
        'BulletDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.6,
        leading=10.2,
        textColor=COLOR_TEXT,
        leftIndent=6,
        spaceAfter=1.5
    )

    table_hdr_style = ParagraphStyle(
        'TableHdr',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.8,
        leading=9.5,
        textColor=colors.white
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=COLOR_TEXT
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9.5,
        textColor=COLOR_NAVY
    )

    story = []

    # Title Banner Block
    banner_data = [
        [Paragraph("Public Safety Drone Crowd Monitor & Risk Platform", title_style)],
        [Paragraph("COMPREHENSIVE OPERATIONAL & SYSTEM USER GUIDE", subtitle_style)]
    ]
    banner_table = Table(banner_data, colWidths=[556])
    banner_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 4),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 4))

    # Executive Summary Box
    exec_summary_data = [
        [Paragraph("<b>EXECUTIVE OVERVIEW</b>", ParagraphStyle('ExecHdr', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, textColor=COLOR_NAVY))],
        [Paragraph(
            "The <b>Public Safety Drone Crowd Monitoring & Risk Platform</b> is an advanced real-time video analytics system "
            "engineered for high-density public event management, pilgrimage safety, and emergency response command centers. "
            "By consuming streams from drone swarms, CCTV cameras, and mobile feeds, the platform dynamically monitors crowd density, "
            "detects erratic directional movement, forecasts spatial buildup trends, and dispatches geotagged field alerts to security teams.",
            body_style
        )]
    ]
    exec_table = Table(exec_summary_data, colWidths=[556])
    exec_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLOR_BG_LIGHT),
        ('BOX', (0,0), (-1,-1), 0.75, COLOR_BORDER),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(exec_table)
    story.append(Spacer(1, 4))

    # Section 1: System Architecture & Flow
    story.append(Paragraph("1. System Architecture & Operational Pipeline", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    arch_data = [
        [Paragraph("<b>Video Ingestion Layer</b>", table_cell_bold), Paragraph("Consumes live video feeds from drone swarms, stationary CCTV cameras, and mobile field transmitters.", table_cell_style)],
        [Paragraph("<b>High-Speed Streaming Server</b>", table_cell_bold), Paragraph("Manages video ingestion, overlays visual HUD analytics, and delivers low-latency command center playback.", table_cell_style)],
        [Paragraph("<b>AI Analytics & Risk Engine</b>", table_cell_bold), Paragraph("Performs continuous density estimation, movement vector tracking, sector risk scoring, and buildup forecasting.", table_cell_style)],
        [Paragraph("<b>Output & Dispatch Layer</b>", table_cell_bold), Paragraph("Powers web portals, high-performance tactical swarm displays, automated mobile alerts, and GIS map exports.", table_cell_style)]
    ]
    arch_table = Table(arch_data, colWidths=[140, 416])
    arch_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, COLOR_BORDER),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, COLOR_BG_LIGHT]),
        ('PADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(arch_table)
    story.append(Spacer(1, 4))

    # Section 2: Core Functional Modules
    story.append(Paragraph("2. Core Operational Modules", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    modules = [
        ("Crowd Density Analytics Engine", "Estimates crowd counts accurately across high-density pilgrimage sectors. Features <b>Dynamic Altitude Compensation</b> to maintain measurement precision as drones change flight elevation."),
        ("Motion Dynamics & Flow Monitoring", "Tracks movement speed. Calculates a <b>Crowd Turbulence Index</b> to detect erratic movement (indicating bottlenecks or panic) and flags <b>Counter-Flow Traffic</b> to prevent head-on crowd collisions."),
        ("Predictive Buildup Forecasting", "Analyzes density history trends to predict crowd buildup across multiple time horizons (from short-term seconds to multi-hour forecasts), allowing proactive crowd control before hazardous congestion forms.")
    ]

    for title, desc in modules:
        story.append(Paragraph(f"• <b>{title}:</b> {desc}", bullet_style))

    story.append(Spacer(1, 4))

    # Section 3: Operational Display Modes
    story.append(Paragraph("3. Operational Display Modes", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    modes_data = [
        [
            Paragraph("<b>Mode 1: Web Command Portal</b>", table_cell_bold),
            Paragraph("Designed for Command Center supervisors. Multi-stream browser portals, interactive spatial heatmaps, and low-latency playback with zero installation required.", table_cell_style)
        ],
        [
            Paragraph("<b>Mode 2: Tactical Swarm Workstation</b>", table_cell_bold),
            Paragraph("Designed for multi-monitor GPU workstations. Displays full-screen visual HUD overlays, motion vector fields, sector capacity meters, and drone fleet management.", table_cell_style)
        ]
    ]
    modes_table = Table(modes_data, colWidths=[160, 396])
    modes_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, COLOR_BORDER),
        ('BACKGROUND', (0,0), (-1,-1), COLOR_BG_LIGHT),
        ('PADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(modes_table)
    story.append(Spacer(1, 4))

    # Section 4: Risk Engine Table
    story.append(Paragraph("4. Sector Risk Rating & Warning Levels", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    risk_table_data = [
        [
            Paragraph("Safety Level", table_hdr_style),
            Paragraph("Risk Score Range", table_hdr_style),
            Paragraph("Visual Badge", table_hdr_style),
            Paragraph("Triggered Operational Actions", table_hdr_style)
        ],
        [
            Paragraph("<b>NORMAL</b>", table_cell_bold),
            Paragraph("0% – 40%", table_cell_style),
            Paragraph("<font color='#16A34A'><b>GREEN</b></font>", table_cell_style),
            Paragraph("Standard monitoring & routine telemetry log.", table_cell_style)
        ],
        [
            Paragraph("<b>MODERATE</b>", table_cell_bold),
            Paragraph("41% – 70%", table_cell_style),
            Paragraph("<font color='#CA8A04'><b>YELLOW</b></font>", table_cell_style),
            Paragraph("Highlight monitored sector; raise telemetry sampling rate.", table_cell_style)
        ],
        [
            Paragraph("<b>WARNING</b>", table_cell_bold),
            Paragraph("71% – 85%", table_cell_style),
            Paragraph("<font color='#EA580C'><b>ORANGE</b></font>", table_cell_style),
            Paragraph("Visual sector flashing alert; alert sector supervisors.", table_cell_style)
        ],
        [
            Paragraph("<b>CRITICAL</b>", table_cell_bold),
            Paragraph("86% – 100%", table_cell_style),
            Paragraph("<font color='#DC2626'><b>RED</b></font>", table_cell_style),
            Paragraph("Automated geotagged field dispatch with GPS pin & stampede warning.", table_cell_style)
        ]
    ]

    risk_table = Table(risk_table_data, colWidths=[90, 100, 80, 286])
    risk_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), COLOR_NAVY),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, COLOR_BORDER),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, COLOR_BG_LIGHT]),
        ('PADDING', (0,0), (-1,-1), 2.5),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(risk_table)
    story.append(Spacer(1, 4))

    # Section 5: Automated Field Dispatch & Incident Management
    story.append(Paragraph("5. Automated Field Dispatch & Incident Management", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    dispatch_info = [
        ("Geotagged Mobile Dispatch", "When a sector breaches <b>CRITICAL</b> threshold, the system dispatches instant field alerts (Telegram/SMS) containing sector location, current density rating, turbulence warnings, and interactive Google Maps GPS pins."),
        ("Municipal GIS Export", "Writes spatial map point and polygon layers to standard GeoJSON formats for seamless integration with municipal GIS command systems.")
    ]

    for title, desc in dispatch_info:
        story.append(Paragraph(f"• <b>{title}:</b> {desc}", bullet_style))

    story.append(Spacer(1, 4))

    # Section 6: Deployment Best Practices
    story.append(Paragraph("6. Operational Deployment Best Practices", h1_style))
    story.append(HRFlowable(width="100%", thickness=0.75, color=COLOR_NAVY, spaceBefore=1, spaceAfter=3))

    practices = [
        ("Camera Angles", "Position drone cameras at top-down nadir angles (60° to 90° downward) to minimize perspective distortion and visual occlusion."),
        ("Stationary Hovering", "Maintain stationary hovering positions during density sampling cycles to ensure reliable optical vector tracking."),
        ("Capacity Tuning", "Configure realistic sector capacity limits in system settings based on physical space layouts (e.g. exit gates vs. open plazas)."),
        ("Network Bonding", "Ensure reliable 5GHz Wi-Fi or LTE cellular bonding routers connect field drones to command stations.")
    ]

    for title, desc in practices:
        story.append(Paragraph(f"• <b>{title}:</b> {desc}", bullet_style))

    # Build Document
    doc.build(story, canvasmaker=PublicGuideCanvas)
    print(f"Successfully generated {filename}")

if __name__ == "__main__":
    build_pdf()
