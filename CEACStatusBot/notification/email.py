from smtplib import SMTP_SSL
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from .handle import NotificationHandle

class EmailNotificationHandle(NotificationHandle):
    def __init__(self,fromEmail:str,toEmail:str,emailPassword:str,hostAddress:str='') -> None:
        super().__init__()
        self.__fromEmail = fromEmail
        self.__toEmail = toEmail.split("|")
        self.__emailPassword = emailPassword
        self.__hostAddress = hostAddress or "smtp."+fromEmail.split("@")[1]
        if ':' in self.__hostAddress:
            [addr, port] = self.__hostAddress.split(':')
            self.__hostAddress = addr
            self.__hostPort = int(port)
        else:
            self.__hostPort = 0
    def format_visa_data_with_tables(self, data):
        """Format the visa data in a readable way, preserving table formatting"""
        
        output = []

        
        # 基本信息
        output.append("\n[APPLICATION DETAILS]")
        output.append("-" * 40)
        output.append(f"Application Number: {data.get('application_num', 'N/A')}")
        output.append(f"Status: {data.get('status', 'N/A')}")
        output.append(f"Case Created: {data.get('case_created', 'N/A')}")
        output.append(f"Case Last Updated: {data.get('case_last_updated', 'N/A')}")

        
        # 处理报告行，包括表格
        
        output.append("[PROCESSING REPORT]")
        output.append("-" * 40)
        
        in_table = False
        table_lines = []
        other_lines = []
        
        # 分离表格行和非表格行
        for line in data.get('reportlines', []):
            if '+' in line and '|' in line:  # 检测表格行
                in_table = True
                table_lines.append(line)
            elif line.strip() == '' and in_table:  # 表格结束
                in_table = False
                table_lines.append(line)
            elif in_table:
                table_lines.append(line)
            else:
                other_lines.append(line)
        
        # 输出非表格内容
        for line in other_lines:
            if line.strip():  # 只输出非空行
                output.append(line)
        # 描述
        output.append("\n[DESCRIPTION]")
        output.append("-" * 40)
        description = data.get('description', '')
        import textwrap
        for line in description.split('\n'):
            if line.strip():  # 只处理非空行
                wrapped = textwrap.fill(line, width=78)
                output.append(wrapped)
        # 输出表格
        if table_lines:
            output.append("\n[DETAILED CASES TABLE]")
            output.append("-" * 40)
            for line in table_lines:
                output.append(line)
        
        return "\n".join(output)


    def send(self,result):
        
        # {'success': True, 'visa_type': 'NONIMMIGRANT VISA APPLICATION', 'status': 'Issued', 'case_created': '30-Aug-2022', 'case_last_updated': '19-Oct-2022', 'description': 'Your visa is in final processing. If you have not received it in more than 10 working days, please see the webpage for contact information of the embassy or consulate where you submitted your application.', 'application_num': '***'}

        mail_title = '[CEACStatusBot] {} : {}'.format(result["application_num_origin"],result['status'])
        mail_content = str(self.format_visa_data_with_tables(result))

        msg = MIMEMultipart()
        msg["Subject"] = Header(mail_title,'utf-8')
        msg["From"] = self.__fromEmail
        msg['To'] = ";".join(self.__toEmail)
        msg.attach(MIMEText(mail_content,'plain','utf-8'))

        smtp = SMTP_SSL(self.__hostAddress, self.__hostPort) # ssl登录
        print(smtp.login(self.__fromEmail,self.__emailPassword))
        print(smtp.sendmail(self.__fromEmail,self.__toEmail,msg.as_string()))
        smtp.quit()
