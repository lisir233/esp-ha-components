# SPDX-FileCopyrightText: 2018-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
import asyncio
from http.client import HTTPConnection, HTTPSConnection
import socket

from ..utils import str_to_bytes

from .transport import Transport


class Transport_HTTP(Transport):
    def __init__(self, hostname, ssl_context=None):
        try:
            socket.getaddrinfo(hostname.split(":")[0], None)
        except socket.gaierror:
            raise RuntimeError(f"Unable to resolve hostname: {hostname}")

        if ssl_context is None:
            self.conn = HTTPConnection(hostname, timeout=None)
        else:
            self.conn = HTTPSConnection(hostname, context=ssl_context, timeout=None)
        try:
            print(f"++++ Connecting to {hostname}++++")
            self.conn.connect()
        except Exception as err:
            raise RuntimeError("Connection Failure : " + str(err))
        self.headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        }

    def _send_post_request(self, path, data):
        """发送 POST 请求（仅发送，不读取响应）
        ✅ 使用原始 socket 发送，避免 http.client 状态机问题
        响应由 HTTPMessageListener 从 socket 直接读取
        """
        data = str_to_bytes(data) if isinstance(data, str) else data
        try:
            # ✅ 构建 HTTP 请求并直接发送到 socket
            # 这样避免 http.client 状态机问题，让 listener 可以接收响应
            if self.conn and self.conn.sock:
                # 提取主机名
                host = self.conn.host
                if self.conn.port and self.conn.port != 80:
                    host_header = f"{host}:{self.conn.port}"
                else:
                    host_header = host

                # 构建 HTTP 请求
                request_line = f"POST {path} HTTP/1.1\r\n"
                headers = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
                headers += f"Host: {host_header}\r\n"
                headers += f"Content-Length: {len(data)}\r\n"
                headers += "\r\n"

                # 发送请求行、头部和数据
                request = (request_line + headers).encode("latin-1") + data
                print(
                    f"[DEBUG] _send_post_request: Sending {len(request)} bytes to {path}"
                )
                self.conn.sock.sendall(request)
                print("[DEBUG] _send_post_request: Sent successfully")
            else:
                print(
                    f"[DEBUG] _send_post_request: Connection not available - conn={self.conn}, sock={self.conn.sock if self.conn else 'None'}"
                )
                raise RuntimeError("Connection not available")
        except Exception as err:
            print(f"[DEBUG] _send_post_request: Error - {err}")
            raise RuntimeError("Connection Failure : " + str(err))

    def _send_post_request_with_response(self, path, data):
        """发送 POST 请求并同步等待响应
        ✅ 用于会话握手和版本检查等需要立即响应的操作
        ⚠️ 不在这里清理 socket 缓冲区，由 reset_connection() 在握手完成后统一清理
        """
        data = str_to_bytes(data) if isinstance(data, str) else data
        try:
            self.conn.request("POST", path, data, self.headers)
            response = self.conn.getresponse()
            # 处理 Set-Cookie 头用于会话维持
            for hdr_key, hdr_val in response.getheaders():
                if hdr_key == "Set-Cookie":
                    self.headers["Cookie"] = hdr_val
            if response.status == 200:
                result = response.read().decode("latin-1")
                # ⚠️ Don't close response - keep connection alive for HTTPMessageListener
                # response.close() would interfere with the persistent connection
                return result
        except Exception as err:
            raise RuntimeError("Connection Failure : " + str(err))
        raise RuntimeError("Server responded with error code " + str(response.status))

    def reset_connection(self):
        """清除 HTTPConnection 的缓冲区污染

        ✅ 握手使用 getresponse() 从 socket 读取了响应
        socket 缓冲区中可能有残留数据

        ⚠️ 关键：保持 socket 打开供 listener 使用
        只清除 http.client 的内部状态和 socket 缓冲区
        """
        try:
            if not self.conn:
                return

            # 清除内部响应引用
            try:
                if hasattr(self.conn, "_HTTPConnection__response"):
                    response_obj = self.conn._HTTPConnection__response
                    if response_obj:
                        try:
                            response_obj.close()
                        except:
                            pass
                    self.conn._HTTPConnection__response = None

                # 清除其他内部状态
                if hasattr(self.conn, "_method"):
                    self.conn._method = None
            except:
                pass

        except Exception:
            pass

    async def send_data(self, ep_name, data):
        """发送数据（仅发送，不等待响应）
        ✅ 用于握手和正常操作 - 响应由 HTTPMessageListener 接收
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_post_request, "/" + ep_name, data)
        # ✅ 不返回响应 - 响应由 HTTPMessageListener 从 socket.recv() 接收

    async def send_data_and_receive(self, ep_name, data):
        """发送数据并接收响应
        ✅ 用于需要立即返回响应的操作（如版本检查）
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._send_post_request_with_response, "/" + ep_name, data
        )

    def close(self):
        """关闭 HTTP 连接，确保发送 FIN 包并清除所有引用"""
        try:
            if self.conn:
                # ✅ 使用 shutdown() 强制发送 FIN 包
                try:
                    if hasattr(self.conn, "sock") and self.conn.sock:
                        import socket as socket_module

                        self.conn.sock.shutdown(socket_module.SHUT_RDWR)
                except Exception:
                    pass

                # 然后关闭 socket
                try:
                    if hasattr(self.conn, "sock") and self.conn.sock:
                        self.conn.sock.close()
                        self.conn.sock = None
                except Exception:
                    pass

                # 然后关闭 HTTPConnection
                try:
                    self.conn.close()
                except Exception:
                    pass

                self.conn = None
        except Exception:
            pass
