export interface Role {
  code: 'operator' | 'supervisor' | 'admin'
  name: string
}

export interface User {
  id: number
  username: string
  display_name: string
  department: string
  module_scope?: string[]
  roles: Role[]
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  user: User
}
